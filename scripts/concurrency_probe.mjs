// Real SDK/QuickJS check scenarios; shares the scripted provider in codemode_probe.
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import {execFileSync} from 'node:child_process';
const quote = value => "'" + value.replaceAll("'", "'\\''") + "'";

export async function runConcurrency({createSession, attach, runCase, report, workerConfig, workdir, paths}) {
  const pressureFile = path.join(workdir, 'pressure.json');
  report.concurrency = {cpus:os.availableParallelism(), scenarios:{}};
  const original = structuredClone(workerConfig);
  execFileSync('git',['init','-q'],{cwd:paths.worktree});
  fs.writeFileSync(path.join(paths.worktree,'.gitignore'),'*.json\n*.started\n');
  execFileSync('git',['add','.gitignore'],{cwd:paths.worktree});
  execFileSync('git',['-c','user.name=Probe','-c','user.email=probe@example.invalid','commit','-qm','synthetic probe'],{cwd:paths.worktree});
  async function scenario(name, width, {keys=false, unknown=false, high=false, oversized=false, cancel=false, deadline=false, dirty=false}={}) {
    const jobs = Array.from({length: cancel || dirty ? 2 : oversized || high ? 1 : 4}, (_,i) => {
      const file = path.join(paths.worktree, `${name}-${i}.json`);
      const startFile = file+'.started';
      const source = `import pathlib,time,json,resource,sys,os,hashlib
p=pathlib.Path(${JSON.stringify(file)})
t=time.monotonic()
pathlib.Path(${JSON.stringify(startFile)}).write_text(str(os.getpid()))
b=bytearray(32*1024*1024)
b[::4096]=b'1'*(len(b)//4096)
h=hashlib.pbkdf2_hmac('sha256',b'x',b'y',800000).hex()
time.sleep(${cancel ? 15 : .35})
u=resource.getrusage(resource.RUSAGE_SELF)
p.write_text(json.dumps(dict(start=t,end=time.monotonic(),pid=os.getpid(),digest=h,peakBytes=u.ru_maxrss*(1 if sys.platform=='darwin' else 1024),cpuSeconds=u.ru_utime+u.ru_stime)))`;
      return {id:`${name}-${i}`,command:`${quote(workerConfig.python)} -c ${quote(source)}`,file,startFile};
    });
    const cfg = {...original, bashDefaultTimeoutSeconds:60, bashCeilingSeconds:120, checkTimeoutSeconds:60,
      checkExecution:{maxConcurrent:width,cpuSlots:Math.min(width,os.availableParallelism()),memoryMiB:256},
      acceptanceItems:jobs.map((j,i)=>({id:j.id,command:j.command,estimatedSeconds:1,
        ...(dirty && i===1 ? {targetedCommand:"true"}:{}),
        checkResources:{parallelSafe:!(dirty && i===0),cpuSlots:1,memoryMiB:oversized ? 999999 : 64,exclusiveKeys:keys?['shared-db']:[]}}))};
    const state=JSON.parse(fs.readFileSync(cfg.deadlinePath,'utf8'));
    state.deadlineAt=Date.now()/1000+(deadline?61.8:300);
    fs.writeFileSync(cfg.deadlinePath,JSON.stringify(state));
    fs.writeFileSync(process.env.CODEX_PI_WORKER_CONFIG,JSON.stringify(cfg));
    if (unknown || high) {
      fs.writeFileSync(pressureFile,JSON.stringify({state:unknown?'unknown':'high'}));
      process.env.CODEX_PI_PRESSURE_SNAPSHOT=pressureFile;
    } else delete process.env.CODEX_PI_PRESSURE_SNAPSHOT;
    const session=await createSession(); attach(session);
    try {
      const code=`const jobs=${JSON.stringify(jobs.map(({id,command})=>({id,command,estimatedSeconds:1,timeoutSeconds:30,final:true})))};
const results=await Promise.all(jobs.map(j=>tools.check(j))); return {results};`;
      await runCase(session,name,[{code},{text:'done'}] ,(cancel || dirty)?async s=>{
        const pending=s.prompt('concurrency cancellation');
        const deadline=Date.now()+10000;
        while (!fs.existsSync(jobs[0].startFile) && Date.now()<deadline) await new Promise(r=>setTimeout(r,10));
        if (!fs.existsSync(jobs[0].startFile)) throw new Error('first check never started');
        if (cancel) await s.abort();
        if (dirty) fs.writeFileSync(path.join(paths.worktree,'dirty.txt'),'changed while queued');
        await pending;
      }:undefined);
    } finally { session.dispose(); if(dirty) fs.rmSync(path.join(paths.worktree,'dirty.txt'),{force:true}); }
    const records=jobs.filter(j=>fs.existsSync(j.file)).map(j=>JSON.parse(fs.readFileSync(j.file,'utf8')));
    const pids=jobs.filter(j=>fs.existsSync(j.startFile)).map(j=>Number(fs.readFileSync(j.startFile,'utf8')));
    let live=0;
    const cleanupDeadline=Date.now()+3500;
    do {
      live=0;
      for (const pid of pids) {try{process.kill(pid,0);live++;}catch{}}
      if (!live) break;
      await new Promise(resolve=>setTimeout(resolve,20));
    } while(Date.now()<cleanupDeadline);
    report.concurrency.scenarios[name]={width,records,started:pids.length,live};
  }
  for(const width of [1,2,4]) await scenario(`parallel-${width}`,width);
  await scenario('shared',2,{keys:true});
  await scenario('unknown',2,{unknown:true});
  await scenario('pressure',2,{high:true});
  await scenario('capacity',2,{oversized:true});
  await scenario('cancel',1,{cancel:true});
  await scenario('deadline',1,{deadline:true});
  await scenario('dirty',2,{dirty:true});
  delete process.env.CODEX_PI_PRESSURE_SNAPSHOT;
}
