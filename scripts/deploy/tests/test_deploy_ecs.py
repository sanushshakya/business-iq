"""scripts/deploy/ecs.sh against a stand-in `aws` (fake_aws.py): deploy, failed migration, failed rollout, rollback."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / 'ecs.sh'
REPO = '000000000000.dkr.ecr.eu-west-2.amazonaws.com/iq'
OLD, NEW, OLDER = 'a' * 40, 'b' * 40, 'c' * 40
SERVICES = ('web', 'worker', 'beat')


def arn(family, revision):
    return f'arn:aws:ecs:eu-west-2:000000000000:task-definition/{family}:{revision}'


def task_def(family, tag, command=None):
    container = {'name': 'app', 'image': f'{REPO}:{tag}', 'essential': True}
    if command:
        container['command'] = command
    sidecar = {'name': 'log-router', 'image': 'public.ecr.aws/aws-observability/aws-for-fluent-bit:3'}
    return {'family': family, 'containerDefinitions': [container, sidecar], 'cpu': '512', 'memory': '1024'}


def initial_state():
    """Each service on revision 2 (tag OLD); revision 1 ran OLDER."""
    services, defs = {}, {}
    for name in SERVICES:
        family = f'iq-staging-{name}'
        defs[arn(family, 1)] = task_def(family, OLDER)
        defs[arn(family, 2)] = task_def(family, OLD)
        services[name] = {
            'taskDefinition': arn(family, 2),
            'launchType': 'FARGATE',
            'networkConfiguration': {'awsvpcConfiguration': {'subnets': ['subnet-1'], 'securityGroups': ['sg-1'],
                                                             'assignPublicIp': 'DISABLED'}},
            'deploymentConfiguration': {'maximumPercent': 100 if name == 'beat' else 200,
                                        'minimumHealthyPercent': 0 if name == 'beat' else 100},
            'deployments': [{'status': 'PRIMARY', 'taskDefinition': arn(family, 2), 'rolloutState': 'COMPLETED'}],
        }
    return {'services': services, 'taskdefs': defs, 'images': [OLDER, OLD, NEW]}


@unittest.skipUnless(shutil.which('jq') and shutil.which('bash'), 'needs jq and bash')
class EcsScriptTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)
        aws = self.dir / 'bin' / 'aws'
        aws.parent.mkdir()
        aws.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{HERE / "fake_aws.py"}" "$@"\n')
        aws.chmod(0o755)
        self.state_file = self.dir / 'state.json'
        self.summary = self.dir / 'summary.md'
        self.state = initial_state()

    def run_script(self, command, **env):
        self.state_file.write_text(json.dumps(self.state))
        full_env = {
            'PATH': f'{self.dir / "bin"}:{os.environ["PATH"]}',
            'FAKE_AWS_STATE': str(self.state_file),
            'CLUSTER': 'iq-staging', 'SERVICES': 'web,worker beat', 'IMAGE_REPO': REPO,
            'SUMMARY_FILE': str(self.summary),
            **env,
        }
        result = subprocess.run(['bash', str(SCRIPT), command], env=full_env, capture_output=True, text=True,
                                timeout=60)
        self.state = json.loads(self.state_file.read_text())
        return result

    def running(self, service):
        return self.state['services'][service]['taskDefinition']

    def image(self, task_def_arn):
        return self.state['taskdefs'][task_def_arn]['containerDefinitions'][0]['image']

    def updates(self):
        return [c for c in self.state.get('calls', []) if c[1] == 'update-service']

    # deploy

    def test_deploy_rolls_every_service_onto_the_new_image_after_migrating(self):
        result = self.run_script('deploy', IMAGE_TAG=NEW)
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in SERVICES:
            self.assertEqual(self.running(name), arn(f'iq-staging-{name}', 3))
            self.assertEqual(self.image(self.running(name)), f'{REPO}:{NEW}')
        # The sidecar keeps its image; the worker keeps its own command.
        sidecar = self.state['taskdefs'][self.running('web')]['containerDefinitions'][1]
        self.assertTrue(sidecar['image'].startswith('public.ecr.aws/'))

        task = self.state['migration_task']
        self.assertEqual(task['task_definition'], arn('iq-staging-web', 3))
        self.assertEqual(task['overrides']['containerOverrides'][0],
                         {'name': 'app', 'command': ['python', 'manage.py', 'migrate', '--noinput']})
        self.assertEqual(task['launch_type'], 'FARGATE')
        self.assertIn('subnet-1', task['network'])
        # Migrations ran before any service moved.
        calls = [c[1] for c in self.state['calls']]
        self.assertLess(calls.index('run-task'), calls.index('update-service'))
        # Exactly one migration task.
        self.assertEqual(calls.count('run-task'), 1)

    def test_deploy_keeps_each_services_percentages_and_switches_on_the_circuit_breaker(self):
        self.run_script('deploy', IMAGE_TAG=NEW)
        beat = self.state['services']['beat']['deploymentConfiguration']
        self.assertEqual(beat['maximumPercent'], 100)
        self.assertEqual(beat['minimumHealthyPercent'], 0)
        self.assertEqual(beat['deploymentCircuitBreaker'], {'enable': True, 'rollback': True})

    def test_summary_names_every_service(self):
        self.run_script('deploy', IMAGE_TAG=NEW)
        text = self.summary.read_text()
        self.assertIn(f'Deployed `{NEW}`', text)
        for name in SERVICES:
            self.assertIn(f'`{name}`', text)

    def test_failed_migration_deploys_nothing(self):
        self.state['migration_exit'] = 1
        result = self.run_script('deploy', IMAGE_TAG=NEW)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('::error title=Migrations failed::Exit code 1', result.stderr)
        self.assertEqual(self.updates(), [])
        for name in SERVICES:
            self.assertEqual(self.running(name), arn(f'iq-staging-{name}', 2))
            # The revision made for this deploy is not left ACTIVE.
            self.assertEqual(self.state['taskdefs'][arn(f'iq-staging-{name}', 3)]['status'], 'INACTIVE')

    def test_an_image_not_in_ecr_is_refused_before_anything_changes(self):
        result = self.run_script('deploy', IMAGE_TAG='d' * 40)
        self.assertIn('::error title=Image not found::', result.stderr)
        self.assertNotIn('register-task-definition', [c[1] for c in self.state['calls']])

    def test_a_missing_service_is_refused(self):
        result = self.run_script('deploy', IMAGE_TAG=NEW, SERVICES='web nope')
        self.assertIn('Service nope is not active', result.stderr)
        self.assertEqual(self.updates(), [])

    def test_rollout_rolled_back_by_ecs_puts_every_service_back_and_fails(self):
        self.state['rolled_back'] = ['worker']
        result = self.run_script('deploy', IMAGE_TAG=NEW)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('::error title=Deployment failed::Not healthy: worker', result.stderr)
        self.assertIn('Every service is back on its previous task definition', result.stderr)
        for name in SERVICES:
            self.assertEqual(self.running(name), arn(f'iq-staging-{name}', 2))
            self.assertEqual(self.state['taskdefs'][arn(f'iq-staging-{name}', 3)]['status'], 'INACTIVE')
        self.assertIn('failed', self.summary.read_text())

    def test_a_service_that_cannot_go_back_is_named(self):
        self.state['stuck'] = ['beat']
        result = self.run_script('deploy', IMAGE_TAG=NEW)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('NOT back on the previous version: beat', result.stderr)
        self.assertEqual(self.running('web'), arn('iq-staging-web', 2))

    def test_a_registration_that_fails_part_way_changes_nothing(self):
        self.state['register_fail'] = ['iq-staging-worker']
        result = self.run_script('deploy', IMAGE_TAG=NEW)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('::error title=Register failed::', result.stderr)
        self.assertEqual(self.updates(), [])
        self.assertNotIn('run-task', [c[1] for c in self.state['calls']])
        # web's new revision is not left ACTIVE
        self.assertEqual(self.state['taskdefs'][arn('iq-staging-web', 3)]['status'], 'INACTIVE')

    def test_an_update_refused_part_way_puts_every_service_back(self):
        self.state['update_fail'] = ['worker']
        result = self.run_script('deploy', IMAGE_TAG=NEW)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Every service is back on its previous task definition', result.stderr)
        for name in SERVICES:
            self.assertEqual(self.running(name), arn(f'iq-staging-{name}', 2))

    def test_a_service_left_on_the_new_revision_keeps_it_active(self):
        self.state['stuck'] = ['beat']
        self.run_script('deploy', IMAGE_TAG=NEW)
        # beat never settled, so some of its tasks may still run revision 3
        self.assertEqual(self.state['taskdefs'][arn('iq-staging-beat', 3)].get('status', 'ACTIVE'), 'ACTIVE')
        self.assertEqual(self.state['taskdefs'][arn('iq-staging-web', 3)]['status'], 'INACTIVE')

    def test_a_migration_that_overruns_is_stopped_and_named(self):
        self.state['migration_running'] = True
        result = self.run_script('deploy', IMAGE_TAG=NEW, MIGRATION_TIMEOUT='0', POLL_SECONDS='0')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('::error title=Migrations timed out::', result.stderr)
        self.assertEqual(self.state['stopped_task'], 'arn:aws:ecs:eu-west-2:000000000000:task/iq/migrate1')
        self.assertEqual(self.updates(), [])

    def test_an_ecr_error_is_not_reported_as_a_missing_image(self):
        self.state['ecr_error'] = True
        result = self.run_script('deploy', IMAGE_TAG=NEW)
        self.assertIn('::error title=ECR check failed::', result.stderr)
        self.assertNotIn('Image not found', result.stderr)

    def test_summary_falls_back_to_github_step_summary(self):
        result = self.run_script('deploy', IMAGE_TAG=NEW, SUMMARY_FILE='', GITHUB_STEP_SUMMARY=str(self.summary))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f'Deployed `{NEW}`', self.summary.read_text())

    def test_migrations_can_be_skipped(self):
        result = self.run_script('deploy', IMAGE_TAG=NEW, RUN_MIGRATIONS='false')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('run-task', [c[1] for c in self.state['calls']])

    # rollback

    def test_rollback_previous_goes_to_the_image_before_the_running_one_without_migrating(self):
        self.run_script('deploy', IMAGE_TAG=NEW)
        result = self.run_script('rollback', TARGET='previous')
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in SERVICES:
            self.assertEqual(self.image(self.running(name)), f'{REPO}:{OLD}')
        self.assertEqual([c[1] for c in self.state['calls']].count('run-task'), 1)  # the deploy's only
        self.assertIn(f'Rolled back to `{OLD}`', self.summary.read_text())

    def test_rollback_previous_skips_a_deploy_that_failed(self):
        self.state['rolled_back'] = ['web']
        self.run_script('deploy', IMAGE_TAG=NEW)  # fails; revision 3 (NEW) is deregistered
        result = self.run_script('rollback', TARGET='previous')
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in SERVICES:
            self.assertEqual(self.image(self.running(name)), f'{REPO}:{OLDER}')

    def test_rollback_to_a_commit(self):
        result = self.run_script('rollback', TARGET=OLDER)
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in SERVICES:
            self.assertEqual(self.image(self.running(name)), f'{REPO}:{OLDER}')

    def test_rollback_to_something_that_is_not_a_commit_is_refused(self):
        result = self.run_script('rollback', TARGET='latest; rm -rf /')
        self.assertIn('::error title=Bad target::', result.stderr)
        self.assertEqual(self.updates(), [])

    def test_rollback_to_a_short_sha_is_refused(self):
        result = self.run_script('rollback', TARGET=OLDER[:7])
        self.assertIn('::error title=Bad target::', result.stderr)
        self.assertIn('full 40-character', result.stderr)
        self.assertEqual(self.updates(), [])

    def test_rollback_with_no_earlier_image(self):
        for name in SERVICES:
            del self.state['taskdefs'][arn(f'iq-staging-{name}', 1)]
        result = self.run_script('rollback', TARGET='previous')
        self.assertIn('::error title=Nothing to roll back to::', result.stderr)
        self.assertEqual(self.updates(), [])

    def test_missing_configuration_is_named(self):
        result = self.run_script('deploy', IMAGE_TAG=NEW, CLUSTER='')
        self.assertIn('CLUSTER is not set', result.stderr)
