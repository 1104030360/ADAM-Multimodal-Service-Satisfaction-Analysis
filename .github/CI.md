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
  -> macOS runner + MySQL 8.4
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
brew install mysql@8.4
export PATH="$(brew --prefix mysql@8.4)/bin:$PATH"
cd myapp
uv sync --locked --all-extras --dev
uv run --locked pytest --mysql-backend=native --require-integration --cov \
  --cov-report=term-missing --cov-report=xml:ci-results/coverage.xml \
  --junitxml=ci-results/pytest.xml
```

Every dependency installs from a prebuilt wheel (sounddevice bundles PortAudio),
so no compiler, SDK, or Homebrew audio library is needed. The Docker alternative
is `docker pull mysql:8.4`, followed by
`uv run --locked pytest --require-integration --cov` from `myapp/`.

After committing and pushing these files, check **Actions → CI**. Once the first
run passes, open **Settings → Rules → Rulesets**, select the branch you merge
into, require pull requests and status checks, and select **macOS tests**.
CI runs automatically; preventing merges also requires this GitHub repository
setting. Creating the files alone does not enable a branch rule.

CI does not yet validate Briefcase packaging, code signing, notarization,
physical devices, the real models, RDS TLS/grants/time zone, or production
connectivity. Add those as separate release or staging checks as they become
available. The fake analysis service must not publish AI explanations as if
they came from real model results.

## CD for the analysis service (planned)

The analysis services are in Git: `services/analyzer/` (the real models,
deployed to EC2) and `services/analysis_service/` (the fake used by local
end-to-end tests). Their database, Hugging Face, and Ollama credentials are
supplied at runtime. The original `service_analyzer/` source contains embedded
credentials and stays ignored.

Status: designed, not implemented yet. Follow-up pull requests add a Linux CI
job for the service tests, the deploy workflow, the EC2 deploy script, and the
migration runner. The AWS and GitHub settings they rely on are made by hand
first.

| Component | Delivery |
| --- | --- |
| macOS desktop app | CI now; later build, sign, notarize, and publish a versioned installer on a release tag. Users install it; it is not deployed to EC2. |
| EC2 analysis service | Merging to `main` builds, tests, and deploys the tested image automatically. A manual workflow run deploys any commit, which is also how to roll back. |
| RDS | Versioned migrations, applied by the same deployment before the service is replaced. Additive migrations run automatically; destructive ones need a manual run. |
| S3 | Bucket configuration is set up by hand. Recordings, charts, transcripts, and reports are application data, not deployment artifacts. |

### Flow

```text
Merge to main that changes the service, migration, or deploy files,
or a manual "Run workflow" for a chosen commit
  -> Linux runner: build the service image (cached layers)
  -> unit + integration tests inside that image (MySQL 8.4, moto)
  -> push the service and migration images to GitHub Container Registry,
     tagged with the commit SHA
  -> GitHub OIDC -> deployment role in AWS (no stored AWS keys)
  -> write the desired commit to SSM Parameter Store
  -> instance running: Run Command executes the deploy script; the job
     prints its log and fails when the script fails
     instance stopped: not a failure; a systemd unit deploys the desired
     commit at the next boot
```

The deploy script runs one at a time on the instance and does nothing when the
desired commit is already running. Otherwise it pulls both images, applies
pending migrations, checks that the tables, columns, and grants the new
version needs exist, keeps the current image as `previous`, and replaces the
API and worker containers. If `/healthz` does not report `models: ready`
within 5 minutes, it restarts `previous` and exits non-zero. Queued jobs wait
in SQS while the worker restarts, and a job in progress is handed back. The
API stops for a few seconds; a desktop that submits then shows Retry.

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

- Migrations are versioned in the repository and run from the instance,
  which already reaches RDS. GitHub never connects to the database.
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
- Pull request checks will apply new migrations twice to the previous schema
  in a disposable MySQL, compare the result with the committed schemas, and
  flag destructive statements.
- Automated RDS backups stay enabled for point-in-time restore.

### Access and secrets

- The OIDC trust policy accepts only this repository's `main` branch. Its
  subject contains the repository name, so renaming the repository requires
  updating the trust policy.
- The deployment role can check whether the instance is running, send Run
  Command to that instance, read the command results, and read and write the
  desired-commit parameter. Nothing else.
- Only the deploy job gets `id-token: write`. GitHub stores no AWS access
  keys; the role ARN, region, and instance ID are repository variables.
- The service configuration (`analysis.env`, with the database password and
  Ollama key) stays on the instance. It is never in images, the repository,
  or test jobs.
- Deployments are serialized and never cancelled midway. Protect `main`
  with required pull requests and passing checks, because every merge that
  changes the service deploys to production.

References:

- [uv in GitHub Actions](https://docs.astral.sh/uv/guides/integration/github/)
- [GitHub-hosted runner capabilities](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)
- [GitHub OIDC with AWS](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws)
- [GitHub Container Registry](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)
- [AWS Systems Manager Run Command](https://docs.aws.amazon.com/systems-manager/latest/userguide/run-command.html)
- [AWS Systems Manager Parameter Store](https://docs.aws.amazon.com/systems-manager/latest/userguide/systems-manager-parameter-store.html)
