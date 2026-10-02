"""Explicit writer-free storage recovery and immutable future-round runtimes.

Neither operation resumes Pi, changes a contract/deadline, rewrites a round or
edits the original frozen tools. CLI entrypoints pass their own helper catalog.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

from pi_archive import initialize_store, read_store, STORE_FILE
from pi_core import atomic, canonical_root, git_common_dir, lock_fd, read_json, require_task_arg
from pi_phase import phase_budget, read_phase_record
from pi_store import (MAX_BOARD_BYTES, _read_bounded_json, board_file_for_common,
                      read_board, validate_board)


def recover_store(repo):
    root=canonical_root(Path(repo)); common=git_common_dir(root)
    board_file=board_file_for_common(common); directory=board_file.parent
    locks=[]
    try:
        for path in [directory/'.admission.lock',directory/'board.lock',directory/'board.queue.lock']:
            locks.append(lock_fd(path,blocking=False))
        original,problem=_read_bounded_json(board_file,MAX_BOARD_BYTES)
        if isinstance(original,dict) and original.get('schemaVersion')==2:
            board=read_store(board_file)
            return {'ok':True,'idempotent':True,'storage':STORE_FILE,'tasks':len(board['cards']),
                    'revision':board['revision'],'resumed':False}
        if problem or validate_board(original):
            raise ValueError(f'cannot recover unverified legacy board: {problem or validate_board(original)}')
        # Include unregistered workers; registration can race after start. The
        # admission lock prevents new official writers after each check.
        inventory=directory/'tasks'
        until=time.monotonic()+10
        if inventory.exists():
            with os.scandir(inventory) as tasks:
                for entry in tasks:
                    if time.monotonic()>until:
                        raise ValueError('writer inventory timed out; no storage was changed')
                    if not entry.is_dir(follow_symlinks=False):continue
                    for name in ['.task.lock','.supervisor.lock']:
                        path=Path(entry.path)/name
                        if path.exists():
                            fd=lock_fd(path,blocking=False);os.close(fd)
        queue_path=directory/'board.queue.json'
        queue,problem=_read_bounded_json(queue_path,131_072)
        if problem=='missing':queue={'schemaVersion':1,'tasks':{}}
        elif problem or not isinstance(queue,dict) or queue.get('schemaVersion')!=1 \
                or not isinstance(queue.get('tasks'),dict):
            raise ValueError(f'cannot recover unverified legacy queue: {problem or "invalid"}')
        history=directory/'legacy';history.mkdir(exist_ok=True)
        for path in [board_file,queue_path]:
            if path.exists():
                raw=path.read_bytes();digest=hashlib.sha256(raw).hexdigest()
                target=history/(path.stem+'-'+digest+'.json')
                if target.exists():
                    if target.read_bytes()!=raw:raise ValueError('legacy archive hash collision')
                else:
                    with target.open('xb') as stream:
                        stream.write(raw);stream.flush();os.fsync(stream.fileno())
                    target.chmod(0o444)
        board_hash=hashlib.sha256(board_file.read_bytes()).hexdigest()
        queue_hash=hashlib.sha256(queue_path.read_bytes() if queue_path.exists() else b'').hexdigest()
        source_hash=hashlib.sha256((board_hash+'\0'+queue_hash).encode()).hexdigest()
        converted=initialize_store(board_file,original,queue,source_hash)
        return {'ok':True,'idempotent':False,'storage':str(directory/STORE_FILE),
                'legacySourceSha256':board_hash,'legacyQueueSha256':queue_hash,
                'importFingerprint':source_hash,'tasks':len(converted['cards']),
                'revision':converted['revision'],'resumed':False,
                'note':'Exact legacy records retained; obsolete control readers fail closed. '
                       'Use the installed board CLI; frozen worker snapshots are unchanged.'}
    finally:
        for fd in reversed(locks):os.close(fd)


def _generation(task_dir,record):
    generation=record.get('generation')
    if not isinstance(generation,str) or len(generation)!=64 \
            or any(c not in '0123456789abcdef' for c in generation):
        raise ValueError('invalid runtime generation identity')
    path=task_dir/'runtime.adoptions'/generation
    manifest=read_json(path/'manifest.json',None)
    if not isinstance(manifest,dict) or manifest.get('generation')!=generation:
        raise ValueError('runtime generation manifest is missing or mismatched')
    hashes=manifest.get('helperHashes')
    if not isinstance(hashes,dict) or not {'pi_task.py','pi_worker.ts','VERSION'}<=set(hashes) \
            or hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest()!=generation:
        raise ValueError('runtime generation does not bind its complete helper catalog')
    task_hash=hashlib.sha256((task_dir/'task.json').read_bytes()).hexdigest()
    if manifest.get('originalTaskSha256')!=task_hash or record.get('originalTaskSha256')!=task_hash:
        raise ValueError('frozen task identity changed after runtime preparation')
    for name,digest in hashes.items():
        if Path(name).name!=name or not isinstance(digest,str):
            raise ValueError('unsafe runtime helper manifest')
        helper=path/'tools'/name
        if helper.is_symlink() or not helper.is_file() or helper.stat().st_size>2_000_000 \
                or hashlib.sha256(helper.read_bytes()).hexdigest()!=digest:
            raise ValueError(f'runtime helper integrity mismatch: {name}')
    return path/'tools'


def runtime_tools(task_dir):
    task_dir=Path(task_dir)
    pointer=task_dir/'runtime.current.json'
    if not pointer.exists():return task_dir/'tools'
    record=read_json(pointer,None)
    if not isinstance(record,dict):raise ValueError('runtime adoption pointer is unreadable')
    return _generation(task_dir,record)


def round_tools(task_dir,round_number):
    task_dir=Path(task_dir)
    config=read_json(task_dir/'rounds'/str(round_number)/'worker.json',{}) or {}
    path=Path(config.get('toolsDir') or task_dir/'tools')
    if path==task_dir/'tools':return path
    relative=path.relative_to(task_dir/'runtime.adoptions')
    if len(relative.parts)!=2 or relative.parts[-1]!='tools':
        raise ValueError('round runtime is outside its immutable generations')
    manifest=read_json(path.parent/'manifest.json',{}) or {}
    return _generation(task_dir,manifest)


def adopt_runtime(repo,task_id,helper_files,source,dry_run=False):
    root=canonical_root(Path(repo));common=git_common_dir(root)
    task_id=require_task_arg(task_id);task_dir=common/'codex-pi/tasks'/task_id
    frozen=read_json(task_dir/'task.json',None)
    if not isinstance(frozen,dict) or frozen.get('task')!=task_id \
            or Path(frozen.get('repo','')).resolve()!=root:
        raise ValueError('runtime recovery requires the exact frozen task/repository identity')
    if not str(frozen.get('runtimeVersion','')).startswith('0.6.'):
        raise ValueError('0.5.x worker snapshots cannot be adopted or migrated')
    locks=[]
    try:
        for path in [common/'codex-pi/.admission.lock',task_dir/'.task.lock',task_dir/'.supervisor.lock']:
            locks.append(lock_fd(path,blocking=False))
        rounds=sorted(int(p.name) for p in (task_dir/'rounds').iterdir() if p.name.isdigit())
        round_dir=task_dir/'rounds'/str(rounds[-1])
        state=read_json(round_dir/'round.state.json',{}) or {}
        if state.get('state')=='unknown':
            from pi_execution import effective_execution
            recovered,evidence=effective_execution(state,round_dir/'round.jsonl',
                                                   terminal_meta=read_meta(round_dir/'round.meta'))
            if recovered in ('completed','failed','cancelled','timed_out','interrupted'):
                # Adoption consults the new bounded evidence in memory only;
                # immutable round/task/helper records remain untouched.
                state=dict(state,state=recovered,executionEvidence=evidence)
        if state.get('state') not in ('completed','failed','cancelled','timed_out','interrupted'):
            raise ValueError('runtime adoption needs a terminal-known round and released writers')
        groups={value for key in ['piPid','supervisorPid'] for value in [state.get(key)]
                if isinstance(value,int) and not isinstance(value,bool) and value>0}
        until=time.monotonic()+1
        while groups:
            try:
                proc=subprocess.run(['ps','-axo','pid=,pgid=,stat='],capture_output=True,text=True,timeout=3)
            except (OSError,subprocess.SubprocessError) as exc:
                raise ValueError(f'cannot verify recorded process release: {exc}') from None
            if proc.returncode!=0:raise ValueError('cannot verify recorded process-group release')
            rows=proc.stdout.splitlines()
            if len(rows)>50_000:raise ValueError('process inventory exceeded its bounded scan')
            live=[r for r in rows if len(r.split())>=3 and r.split()[2][0]!='Z'
                  and (int(r.split()[0]) in groups or int(r.split()[1]) in groups)]
            if not live:break
            if time.monotonic()>=until:
                raise ValueError('recorded PID/group still exists; ownership is unknown, inspect without blind signaling')
            time.sleep(0.05)
        # Claims and the immutable task/session/worktree identity are rechecked
        # again by continue. Preparation never extends its original budget.
        board,problem=read_board(board_file_for_common(common))
        if board is None:raise ValueError(f'board recovery is required first: {problem}')
        card=board['cards'].get(task_id)
        if not isinstance(card,dict):raise ValueError('task owner route is not registered')
        record,_problem=read_phase_record(task_dir)
        budget=phase_budget(task_dir,record)
        hashes={name:hashlib.sha256((Path(source)/name).read_bytes()).hexdigest() for name in helper_files}
        generation=hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest()
        task_hash=hashlib.sha256((task_dir/'task.json').read_bytes()).hexdigest()
        response={'ok':True,'task':task_id,'generation':generation,'dryRun':bool(dry_run),
                  'ownerThread':card.get('ownerThread'),'paused':bool(card.get('paused')),
                  'sessionId':frozen.get('sessionId'),'sessionDir':frozen.get('sessionDir'),
                  'worktree':frozen.get('worktree'),'quality':review_policy_for(card),
                  'budget':budget,'resumed':False}
        if dry_run:return response
        target=task_dir/'runtime.adoptions'/generation
        manifest={'schemaVersion':1,'generation':generation,'originalTaskSha256':task_hash,
                  'helperHashes':hashes,'runtimeVersion':(Path(source)/'VERSION').read_text().strip()}
        if not target.exists():
            target.parent.mkdir(parents=True,exist_ok=True)
            prepared=Path(tempfile.mkdtemp(prefix='.pending-',dir=target.parent))
            tools=prepared/'tools';tools.mkdir()
            for name in helper_files:
                shutil.copy2(Path(source)/name,tools/name);(tools/name).chmod(0o444)
            atomic(prepared/'manifest.json',manifest);(prepared/'manifest.json').chmod(0o444)
            prepared.rename(target)
        _generation(task_dir,manifest)
        atomic(task_dir/'runtime.current.json',manifest)
        response['runtimeTools']=str(target/'tools')
        return response
    finally:
        for fd in reversed(locks):os.close(fd)


def review_policy_for(card):
    from pi_takeover import review_policy
    return review_policy(card)


def read_meta(path):
    from pi_summary import read_meta as read
    return read(path)
