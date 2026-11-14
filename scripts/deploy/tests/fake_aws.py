"""
A stand-in for the `aws` CLI, just enough of ECS and ECR for scripts/deploy/ecs.sh.

State is a JSON file named by FAKE_AWS_STATE; every call is appended to its "calls". The scenario keys:
  images           tags present in ECR
  migration_exit   exit code of the migration container (default 0)
  rolled_back      services whose next rollout onto a new task definition the circuit breaker undoes
  stuck            services that never become stable on anything
"""

import json
import os
import sys


def option(args, name, default=None):
    if name in args:
        return args[args.index(name) + 1]
    return default


def options(args, name):
    """All values after `name` up to the next --option (for --services a b c)."""
    if name not in args:
        return []
    values = []
    for value in args[args.index(name) + 1:]:
        if value.startswith('--'):
            break
        values.append(value)
    return values


def deployment(task_def, state='COMPLETED'):
    return [{'status': 'PRIMARY', 'taskDefinition': task_def, 'rolloutState': state}]


def main(argv):
    path = os.environ['FAKE_AWS_STATE']
    with open(path) as f:
        state = json.load(f)
    state.setdefault('calls', []).append(argv)
    out, code = handle(state, argv)
    with open(path, 'w') as f:
        json.dump(state, f, indent=1)
    if out is not None:
        print(json.dumps(out))
    return code


def handle(state, argv):
    service, command, args = argv[0], argv[1], argv[2:]
    services, defs = state['services'], state['taskdefs']

    if service == 'ecr' and command == 'describe-images':
        tag = option(args, '--image-ids').split('=', 1)[1]
        if tag in state.get('images', []):
            return {'imageDetails': [{'imageTags': [tag]}]}, 0
        print('An error occurred (ImageNotFoundException)', file=sys.stderr)
        return None, 254

    if command == 'describe-services':
        names = options(args, '--services')
        return {'services': [dict(services[n], serviceName=n, status='ACTIVE') for n in names if n in services],
                'failures': [{'arn': n, 'reason': 'MISSING'} for n in names if n not in services]}, 0

    if command == 'describe-task-definition':
        arn = option(args, '--task-definition')
        if arn not in defs:
            return None, 254
        return {'taskDefinition': dict(defs[arn], taskDefinitionArn=arn, revision=int(arn.rsplit(':', 1)[1]),
                                       status=defs[arn].get('status', 'ACTIVE'))}, 0

    if command == 'register-task-definition':
        with open(option(args, '--cli-input-json')[len('file://'):]) as f:
            body = json.load(f)
        family = body['family']
        revision = 1 + max(int(a.rsplit(':', 1)[1]) for a in defs if a.split('/')[-1].split(':')[0] == family)
        arn = f'arn:aws:ecs:eu-west-2:000000000000:task-definition/{family}:{revision}'
        defs[arn] = body
        return {'taskDefinition': dict(body, taskDefinitionArn=arn)}, 0

    if command == 'deregister-task-definition':
        defs[option(args, '--task-definition')]['status'] = 'INACTIVE'
        return {}, 0

    if command == 'list-task-definitions':
        family = option(args, '--family-prefix')
        arns = [a for a, d in defs.items()
                if d['family'] == family and d.get('status', 'ACTIVE') == 'ACTIVE']
        arns.sort(key=lambda a: int(a.rsplit(':', 1)[1]), reverse=option(args, '--sort') == 'DESC')
        return {'taskDefinitionArns': arns}, 0

    if command == 'run-task':
        state['migration_task'] = {'task_definition': option(args, '--task-definition'),
                                   'overrides': json.loads(option(args, '--overrides')),
                                   'network': option(args, '--network-configuration'),
                                   'launch_type': option(args, '--launch-type')}
        return {'tasks': [{'taskArn': 'arn:aws:ecs:eu-west-2:000000000000:task/iq/migrate1'}], 'failures': []}, 0

    if command == 'describe-tasks':
        name = state['migration_task']['overrides']['containerOverrides'][0]['name']
        exit_code = state.get('migration_exit', 0)
        return {'tasks': [{'containers': [{'name': name, 'exitCode': exit_code}],
                           'stoppedReason': 'Essential container in task exited'}]}, 0

    if command == 'update-service':
        name, arn = option(args, '--service'), option(args, '--task-definition')
        svc = services[name]
        svc['deploymentConfiguration'] = json.loads(option(args, '--deployment-configuration'))
        if name in state.get('stuck', []):
            svc['deployments'] = deployment(arn, 'IN_PROGRESS')
            svc['taskDefinition'] = arn
        elif name in state.get('rolled_back', []) and arn != svc['taskDefinition']:
            state['rolled_back'].remove(name)
            svc['deployments'] = deployment(svc['taskDefinition'])
        else:
            svc['taskDefinition'] = arn
            svc['deployments'] = deployment(arn)
        return {'service': svc}, 0

    if command == 'wait':
        if args[0] == 'services-stable':
            stuck = set(state.get('stuck', [])) & set(options(args, '--services'))
            if stuck:
                print('Waiter ServicesStable failed: Max attempts exceeded', file=sys.stderr)
                return None, 255
        return None, 0

    print(f'fake aws: unsupported {argv}', file=sys.stderr)
    return None, 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
