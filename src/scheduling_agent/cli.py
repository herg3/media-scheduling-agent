"""Command line entry points. Evidence paths are exclusive per invocation."""
import argparse
import json
import math
import os
from pathlib import Path
import uuid
from pydantic import ValidationError
from .agent import run_agent
from .contracts import DomainError
from .evidence import exit_code
from .model import LiveModel, MockModel
from .repository import Repository, request_model


def budgets():
    values = {}
    for env, name, default, integer in [('MAX_MODEL_STEPS','max_model_steps',24,True),('MAX_TOOL_CALLS','max_tool_calls',20,True),('MAX_RUNTIME_SECONDS','max_runtime_seconds',180,False)]:
        value = float(os.environ.get(env, default))
        if not math.isfinite(value) or value <= 0 or (integer and value != int(value)):
            raise ValueError('Invalid budget')
        values[name] = int(value) if integer else value
    return values


def model_for(backend):
    return MockModel() if backend == 'mock' else LiveModel.from_env()


def reserve_output(path):
    path = Path(path)
    if path.exists() and any(path.iterdir()):
        raise FileExistsError('Evidence directory is not empty')
    path.mkdir(parents=True, exist_ok=True)
    return path


def error_output(directory, backend, code):
    value = {'run_id':'RUN-'+uuid.uuid4().hex,'backend':backend,'run_status':'incomplete','error':{'code':code,'message':'输入或确认无效。' if code in {'INVALID_INPUT','CONFIRMATION_MISMATCH','IDEMPOTENCY_CONFLICT'} else '服务或本地存储不可用。'},'items':[]}
    (directory/'output.json').write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    return exit_code(value)


def main(argv=None):
    parser = argparse.ArgumentParser(prog='scheduling_agent')
    commands = parser.add_subparsers(dest='command',required=True)
    init = commands.add_parser('init-db'); init.add_argument('--data-dir',default='data'); init.add_argument('--db',required=True)
    show = commands.add_parser('show-draft'); show.add_argument('--draft-id',required=True); show.add_argument('--db',required=True)
    for name in ('run','confirm'):
        cmd = commands.add_parser(name)
        cmd.add_argument('--backend',choices=('mock','live'),required=True); cmd.add_argument('--db',required=True); cmd.add_argument('--output-dir',required=True)
        if name == 'run': cmd.add_argument('--input',required=True)
        else:
            cmd.add_argument('--draft-id',required=True); cmd.add_argument('--revision',required=True,type=int); cmd.add_argument('--proposed-at',required=True)
    ev = commands.add_parser('evaluate'); ev.add_argument('--backend',choices=('mock','live'),required=True); ev.add_argument('--cases',default='examples/cases'); ev.add_argument('--case-ids'); ev.add_argument('--output-dir',required=True); ev.add_argument('--repeats',type=int,default=1)
    args = parser.parse_args(argv)
    directory = None; repo = None
    try:
        if args.command in {'run','confirm','evaluate'}:
            directory = reserve_output(args.output_dir)
            limits = budgets()
        if args.command == 'init-db':
            repo = Repository.initialize(args.db,args.data_dir)
            print(json.dumps({'initialized':True})); return 0
        if args.command == 'evaluate':
            from .evaluation import evaluate
            summary = evaluate(args.backend,args.cases,directory,case_ids=args.case_ids.split(',') if args.case_ids else None,repeats=args.repeats,limits=limits)
            print(json.dumps({'total':summary['total'],'failed':summary['failed']}))
            return 4 if summary['failed'] else 0
        if args.command == 'run':
            try:
                request = json.loads(Path(args.input).read_text(encoding='utf-8-sig'))
            except (OSError, UnicodeError) as error:
                raise ValueError('Unreadable business input') from error
        repo = Repository(args.db)
        if args.command == 'show-draft':
            print(json.dumps(repo.get_draft_by_id(args.draft_id),ensure_ascii=False)); return 0
        confirmation = None
        if args.command == 'confirm':
            confirmation = repo.confirmation(args.draft_id,args.revision,args.proposed_at)
            request = repo.get_request(confirmation['request_id'])
        request = request_model(request)
        repo.register_request(request)
        result = run_agent(request,model_for(args.backend),repo,confirmation=confirmation,output_dir=directory,**limits)
        print(json.dumps({'run_id':result['run_id'],'run_status':result['run_status']},ensure_ascii=False))
        return exit_code(result)
    except FileExistsError:
        print('Refusing to overwrite existing data or evidence.'); return 3
    except (ValueError,ValidationError,KeyError,TypeError):
        return error_output(directory,getattr(args,'backend',None),'INVALID_INPUT') if directory else 3
    except DomainError as error:
        return error_output(directory,getattr(args,'backend',None),error.code) if directory else 3
    except Exception:
        return error_output(directory,getattr(args,'backend',None),'TOOL_UNAVAILABLE') if directory else 4
    finally:
        if repo: repo.close()
