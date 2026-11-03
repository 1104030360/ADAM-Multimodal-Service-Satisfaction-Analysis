# ADAM CI and deployment

## GitHub Actions CI

The [CI workflow](workflows/ci.yml) runs on every pull request, pushes to `main`
and `master`, and manual **Run workflow** requests. One macOS
job installs Python 3.13 and uv 0.13.6, installs the committed dependency versions
with `uv sync --locked`, then runs the unit and integration suites together.
An out-of-date lock file fails the build. Branch coverage must meet the existing
75% threshold in `myapp/pyproject.toml`.

```text
Push / pull request
  -> macOS runner + PortAudio + MySQL 8.4
  -> uv sync --locked
  -> unit tests + integration tests + branch coverage
  -> pytest.xml and coverage.xml artifacts (14 days)
  -> required check: macOS tests
```

This repository's lock file supports macOS only. GitHub's hosted Apple Silicon
macOS runners do not support nested virtualization, so the job uses native
MySQL 8.4 instead of Docker. The test fixture creates its own temporary data
directory, random loopback port, socket, and random password, then stops the
process and deletes its files. It does not use a Homebrew service or an existing
MySQL database. Local Docker tests remain the default.

S3 is mocked with moto, analysis HTTP requests use the existing local fake, and
cameras and microphones use the existing fakes. No AWS account, GitHub secret,
RDS connection, EC2 instance, or real S3 bucket is needed. Tests disable `.env`
loading and isolate the AWS credential files. `--require-integration` makes
missing test prerequisites fail CI instead of silently skipping the DB tests.

The schema is now committed at `myapp/tests/fixtures/schema.sql`, containing the
14 tables, stored procedures, and two synthetic placeholder accounts required
by the tests. It was extracted from the local rebuild schema through migrations
001–007, without the optional production-user grants. The analysis services'
integration and end-to-end tests load the same file. Update this test schema
alongside future schema changes. It is a test fixture, not a production migration.

To reproduce CI on a Mac:

```bash
brew install portaudio mysql@8.4
export PATH="$(brew --prefix mysql@8.4)/bin:$PATH"
export SDKROOT="$(xcrun --sdk macosx --show-sdk-path)"
cd myapp
uv sync --locked --all-extras --dev
uv run --locked pytest --mysql-backend=native --require-integration --cov \
  --cov-report=term-missing --cov-report=xml:ci-results/coverage.xml \
  --junitxml=ci-results/pytest.xml
```

Use a stable macOS SDK if the local Xcode default selects a beta SDK (see the
root README). The Docker alternative is `docker pull mysql:8.4`, followed by
`uv run --locked pytest --require-integration --cov` from `myapp/`.

CI does not yet validate Briefcase packaging, code signing, notarization,
physical devices, the real models, RDS TLS/grants/time zone, or production
connectivity. Add those as separate release or staging checks as they become
available. The fake analysis service must not publish AI explanations as if
they came from real model results.

## Service checks

The [Services workflow](workflows/services.yml) checks the analysis services,
the database migrations, the EC2 deploy script, and the workflows themselves
on Linux runners. It runs on every pull request, pushes to `main` and
`master`, and manual **Run workflow** requests.

Every job runs on every pull request, so each one can be a required check, but
a job whose files did not change stops after its **Decide whether to run**
step and passes in seconds ([changed_paths.sh](scripts/changed_paths.sh)). For
a pull request it compares GitHub's merge commit with the base; for a push,
with the previous tip of the branch; a manual run checks everything. The
workflow has no `paths` filter on purpose: a required check that never starts
leaves the pull request waiting.

| Job (check name) | What it runs | Pull request | Push to `main` | Manual run |
| --- | --- | --- | --- | --- |
| Analyzer image tests | Builds `services/analyzer` for `linux/amd64` (about 6 GB) and runs all of its tests inside that image | When `services/analyzer/`, the test schema, or its CI scripts change | Skipped: Deploy analyzer tests the image it pushes | Yes |
| Fake service tests | The same for `services/analysis_service/` | When `services/analysis_service/`, the test schema, or its CI scripts change | Same rule | Yes |
| Shared code copies | `check_report_copies.py`: the files both services share are identical | Always | Always | Yes |
| Migration checks | `services/db/tests/run.sh`; `services/db/tools/verify.sh --base-ref <base>`; builds the migration image and smoke-tests it | When `services/db/`, the test schema, or its CI scripts change | Same rule | Yes (base: `origin/main`) |
| Deploy script tests | `deploy/tests/run.sh` | When `deploy/` changes | Same rule | Yes |
| Shell and workflow checks | shellcheck 0.11.0 on every tracked shell script (zsh scripts are skipped: shellcheck cannot parse them), actionlint 1.7.12 on the workflows, and the tests of `.github/scripts/` | Always | Always | Yes |

- **Tests inside the image.** [test_service_image.sh](scripts/test_service_image.sh)
  starts a throwaway MySQL 8.4 loaded with `myapp/tests/fixtures/schema.sql`
  and the service's least-privilege `tests/integration/grants.sql`, on an
  internal Docker network (no internet, no published ports), then runs
  `python -m pytest --cov` in the image as its non-root `app` user, using the
  code built into the image. S3 and SQS are the tests' own moto. `pytest.xml`
  and `coverage.xml` are kept as artifacts for 14 days.
- **Build cache.** Images are built with Buildx and the GitHub Actions cache
  (one scope per image). Caches written on `main` are shared with pull
  requests; a pull request's cache is visible only to that pull request. The
  analyzer job first deletes unused SDKs on the hosted runner to make room.
- **Migration image smoke test.** [test_migrations_image.sh](scripts/test_migrations_image.sh)
  checks the interface the deploy script relies on: exit code `2` without
  `DB_HOST`, `7` when the database does not answer, and `0` for `status` as
  `adam_migrator` (production privileges, TLS) on a database loaded from
  `services/db/schema.sql`. Every run must end with a
  `migrate: result=… code=…` line and must not print the password.
- **Upgrade check base.** Pull request: the base the merge commit was made
  from. Push: the previous tip. Manual run: `origin/main`.

### Required checks

After the first runs pass, open **Settings → Rules → Rulesets**, add a ruleset
for `main`, require a pull request before merging, require status checks to
pass, and add these checks (the names are stable):

- `macOS tests`
- `Analyzer image tests`
- `Fake service tests`
- `Shared code copies`
- `Migration checks`
- `Deploy script tests`
- `Shell and workflow checks`

Every merge that changes the service, the migrations, or the deploy script
deploys to production, so set this up before relying on automatic deployment.
Creating the workflow files alone does not enable a branch rule.

To reproduce the service checks locally, from the repository root with Docker
running:

```bash
docker build -t adam-analysis:local services/analysis_service
.github/scripts/test_service_image.sh adam-analysis:local services/analysis_service
python3 -I services/analysis_service/tools/check_report_copies.py
.github/scripts/tests/run.sh    # workflow script tests: needs git and jq, no Docker or AWS
actionlint
shellcheck .github/scripts/*.sh .github/scripts/tests/*.sh .github/scripts/tests/fake_aws
```

`services/analyzer/` works the same way; its image is about 6 GB.

## CD for the analysis service

The analysis services are in Git: `services/analyzer/` (the real models,
deployed to EC2) and `services/analysis_service/` (the fake used by local
end-to-end tests). Their database, Hugging Face, and Ollama credentials are
supplied at runtime. The original `service_analyzer/` source contains embedded
credentials and stays ignored. Database migrations and their runner are in
`services/db/`; the script that deploys on the instance is in `deploy/`.

| Component | Delivery |
| --- | --- |
| macOS desktop app | CI now; later build, sign, notarize, and publish a versioned installer on a release tag. Users install it; it is not deployed to EC2. |
| EC2 analysis service | [Deploy analyzer](workflows/deploy-analyzer.yml): merging to `main` builds, tests, and deploys the tested image automatically. A manual run deploys any commit on `main`, which is also how to roll back. |
| RDS | Versioned migrations in `services/db/`, applied by the same deployment before the service is replaced. Additive migrations run automatically; destructive ones need a manual run. |
| S3 | Bucket configuration is set up by hand. Recordings, charts, transcripts, and reports are application data, not deployment artifacts. |

### Flow

```text
Merge to main that changes services/analyzer/, services/db/, deploy/, or the workflow,
or a manual "Run workflow" for a commit on main
  -> Resolve commit: only main; the commit must be on main
  -> Build, test, and push images (skipped when GHCR already has both images):
       build adam-analyzer and adam-db-migrations for linux/amd64 (cached layers)
       all analyzer tests inside that image (MySQL 8.4, moto); migration image smoke test
       push both to ghcr.io/<owner>/<image>:<commit> only after both passed
  -> Deploy to EC2:
       the instance can pull both images anonymously? (else stop, nothing changed)
       GitHub OIDC -> deployment role in AWS (no stored AWS keys)
       write the commit to /adam/analyzer/desired-commit (SSM Parameter Store)
       instance running: Run Command runs that commit's deploy/deploy.sh and the
         job prints its output and fails when it fails
       instance stopped, stopping, or pending: success; adam-deploy.service
         deploys the desired commit at boot
```

| Job | Push to `main` | Manual run | Pull request |
| --- | --- | --- | --- |
| Resolve commit | Yes | Yes; fails at once when started from another branch | The workflow does not run |
| Build, test, and push images | When GHCR lacks an image for the commit | Same | — |
| Deploy to EC2 | Yes | Yes, unless `build_only` is ticked | — |

- **One run at a time.** Runs are serialized and never cancelled midway; a
  newer run waits, and GitHub keeps only the newest waiting run. Cancelling a
  run does not stop `deploy.sh` on the instance: it finishes or rolls back on
  its own.
- **The tested image is the deployed image.** Each image is built once,
  tested in that image, and pushed only after both images passed; the push
  step checks that the tag still points at the tested image ID. Tags are the
  full commit SHA and are never overwritten: when GHCR already has both images
  for a commit (a redeploy, a rollback, or a rerun), the build is skipped and
  the instance gets exactly the image that was tested before. Rebuilding would
  produce a different image (newer base image and system packages) under the
  same tag. To rebuild a commit anyway, delete that version in the package's
  settings on GitHub first. Images carry the `org.opencontainers.image.source`
  and `org.opencontainers.image.revision` labels.
- **Only `main` deploys.** The OIDC trust policy accepts
  `repo:<owner>/<repo>:ref:refs/heads/main` only, and the deploy job sets no
  `environment:` (an environment changes the OIDC subject and AWS would
  refuse the role). A run started from another branch fails in **Resolve
  commit** and says so.
- **On the instance.** The workflow sends `AWS-RunShellScript` with a
  30-minute limit: it downloads `deploy/deploy.sh` from the deployed commit
  into `/opt/adam/deploy.sh` (a new file, so a running copy is unaffected) and
  runs `/opt/adam/deploy.sh --retry-failed`, plus
  `--allow-destructive-migrations` when that box is ticked. Right after a
  boot the SSM agent may not be online yet (`InvalidInstanceId`); the job
  retries for 5 minutes. The job then polls the result, prints the output
  with workflow commands disabled, and writes the last line,
  `deploy: result=… code=… commit=…`, with an explanation to the summary.

| `deploy.sh` exit code | Meaning | Previous version still running? |
| --- | --- | --- |
| 0 | Deployed, or nothing to do | — |
| 10 | Another `deploy.sh` holds the lock (for example the boot unit); rerun later | Yes |
| 11 | Preflight failed: no `/opt/adam/analysis.env`, Docker not running, low disk, or Parameter Store unreadable | Yes |
| 12 | Image pull failed (package not public, missing tag) | Yes |
| 13 | A destructive migration is pending; see below | Yes |
| 14 | A migration failed | Yes |
| 15 | `check-db` failed: tables, columns, procedures, or grants the new version needs are missing | Yes |
| 16 | No `models: ready` within 5 minutes; rolled back | Yes (the previous one) |
| 17 | The rollback failed too: check the instance now | Not necessarily |

At boot, `adam-deploy.service` runs `deploy.sh` without `--retry-failed`, so a
commit that already failed is not retried at every boot; a workflow run always
retries it. The deploy script itself (lock, migrations, `check-db`, health
check, rollback, logs in `/var/log/adam-deploy.log`) is described in
`deploy/README.md`.

### First deployment

The AWS and GitHub settings come first (`docs/rebuild/cd/PLAN.md` section 8,
on the development machine): the OIDC provider, the `adam-github-deploy`
role, the instance role's permissions, the repository variables
`AWS_DEPLOY_ROLE_ARN`, `AWS_REGION`, and `EC2_INSTANCE_ID`, the
`adam_migrator` account and its Parameter Store password, and RDS backups.
Then:

1. Install the deploy script on the instance once, as `deploy/README.md`
   describes (`deploy/install.sh`: moves `analysis.env` to `/opt/adam/` and
   installs `adam-deploy.service`).
2. Merge the pull request that adds these files. **Deploy analyzer** starts
   on its own, builds, tests, and pushes both images, then stops in **Deploy
   to EC2** at *Check that the instance can pull both images*, because new
   GHCR packages are private. Nothing on AWS has changed at that point. (A
   manual run with `build_only` does the same without the failing job.)
3. Make both packages public, once each: your GitHub profile → **Packages** →
   `adam-analyzer` → **Package settings** → **Change visibility** → **Public**;
   then the same for `adam-db-migrations`. A public package cannot be made
   private again.
4. Open the failed run and choose **Re-run failed jobs**. Only the deploy job
   runs again; the images are not rebuilt. The first migration run records
   001–007 as already applied (they were applied by hand) without running them.
5. Check the job summary: the last line should be
   `deploy: result=… code=0 commit=…`.
6. Add the required checks (above), then merge a small change under
   `services/analyzer/` through a pull request to see the automatic path.

### Manual deployment and rollback

**Actions → Deploy analyzer → Run workflow**, with **Use workflow from** set
to `main`:

- `commit`: the 40-character SHA of a commit on `main`; empty deploys the
  latest commit on `main`. A rollback is a run with an older commit. If GHCR
  has its images, nothing is rebuilt; otherwise they are built and tested
  first. Commits from before this workflow existed cannot be deployed.
- `allow_destructive_migrations`: see below.
- `build_only`: build, test, and push without deploying.

Rolling back the service does not roll the database back. That is safe
because automatic migrations only add things, so older code keeps working; the
migration runner reports migrations it does not know (newer than the image)
and continues. Use **Re-run failed jobs** after fixing a cause outside the
code (for example after starting the SSM agent): it reruns only the deploy job.

### Destructive migrations

A migration that drops, renames, or retypes something, or deletes rows
without `WHERE`, is never applied automatically: `deploy.sh` stops with exit
code 13 and the previous version keeps running. To apply it:

1. Take an RDS snapshot: **RDS → Databases → your database → Actions → Take
   snapshot**.
2. **Run workflow** on `main` with the same commit (or empty for the latest)
   and tick `allow_destructive_migrations`.

### Why GitHub Container Registry

GHCR container storage and transfer are currently free, and public packages
can be pulled without credentials, so the instance needs no registry token.
ECR would keep all access in IAM, but storage beyond its free tier is billed
against the account's credits. Building on the instance would deploy an image
other than the tested one and compete with the running worker for 2 vCPUs and
8 GiB of memory. Moving to ECR later changes only where the workflow pushes
and where the instance pulls.

There is no staging environment: the project runs one instance on the AWS
Free plan. The safeguards are tests inside the release image, the health
check with automatic rollback, and backward-compatible migrations.

### Database migrations

- Migrations are versioned in `services/db/migrations/` and run from the
  instance, which already reaches RDS, by the `adam-db-migrations` image.
  GitHub never connects to the database.
- A `tbl_schema_migration` table records each applied file with its SHA-256.
  The runner applies pending files in order under a MySQL lock, and stops if
  an applied file has changed.
- Only additive migrations (new tables, columns, indexes, procedures, grants)
  run automatically, so the previous image still works after a rollback.
  A file with destructive statements (`DROP TABLE`, `DROP COLUMN`, renames,
  column type changes, `TRUNCATE`, `DELETE`/`UPDATE` without `WHERE`) stops
  the deployment while the old version keeps running. It runs only from a
  manual workflow run, after an RDS snapshot.
- A dedicated migration user has schema privileges on the application
  database only, without `DROP` or user creation. Its password is a Parameter
  Store SecureString that only the instance role can read.
- **Migration checks** apply each pull request's new migrations to the
  previous schema in a disposable MySQL, compare the result with the committed
  schemas, and flag destructive statements.
- Automated RDS backups stay enabled for point-in-time restore.

### Access and secrets

- The OIDC trust policy accepts only this repository's `main` branch. Its
  subject contains the repository name, so renaming the repository requires
  updating the trust policy.
- The deployment role can check whether the instance is running, send Run
  Command to that instance, read the command results, and read and write the
  desired-commit parameter. Nothing else.
- Each job gets only the token permissions it needs: `contents: read`
  everywhere, `packages: read` to look up existing images, `packages: write`
  only in the job that pushes, and `id-token: write` only in the deploy job.
  GitHub stores no AWS access keys; the role ARN, region, and instance ID are
  repository variables, not secrets.
- Workflow inputs and commit messages reach scripts only through environment
  variables, never by `${{ }}` inside a script, and output from the instance
  is printed with workflow commands disabled.
- Actions are pinned to commit SHAs; shellcheck and actionlint are pinned
  releases verified by SHA-256.
- The service configuration (`analysis.env`, with the database password and
  Ollama key) stays on the instance. It is never in images, the repository,
  or test jobs.
- Deployments are serialized and never cancelled midway. Protect `main`
  with required pull requests and passing checks, because every merge that
  changes the service deploys to production.

References:

- [uv in GitHub Actions](https://docs.astral.sh/uv/guides/integration/github/)
- [GitHub-hosted runner capabilities](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)
- [Required status checks and skipped jobs](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks)
- [GitHub OIDC with AWS](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws)
- [GitHub Container Registry](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)
- [Docker build cache in GitHub Actions](https://docs.docker.com/build/ci/github-actions/cache/)
- [AWS Systems Manager Run Command](https://docs.aws.amazon.com/systems-manager/latest/userguide/run-command.html)
- [AWS Systems Manager Parameter Store](https://docs.aws.amazon.com/systems-manager/latest/userguide/systems-manager-parameter-store.html)
