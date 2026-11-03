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

## Should this project have CD?

Use CI now. The EC2 analysis service's source and Dockerfile are now in this
repository (`services/analyzer/`), so continuous delivery can build it from a
reviewed commit.
Start with an explicit production release action. Automatic production
deployment on every push is premature while the real models and packaging
are still being validated.

| Component | Appropriate delivery flow |
| --- | --- |
| macOS desktop app | CI first; later build, sign, notarize, and publish a versioned installer on a release tag. Users install it; this is separate from EC2 deployment. |
| EC2 analysis service | Test a container, publish it to ECR, deploy a tested image digest to staging, then explicitly promote that same digest to production. |
| RDS | Version migrations separately, test them against disposable MySQL, and apply reviewed forward migrations as a release step. Preserve stored-procedure grants after DROP/CREATE. |
| S3 | Deliver bucket configuration through infrastructure code. Runtime recordings, charts, transcripts, and reports are application data, not deployment artifacts. |

The reviewed analysis services are in Git: `services/analyzer/` (the real
models, deployed to EC2) and `services/analysis_service/` (the fake used by
local end-to-end tests). Their database, Hugging Face, and Ollama credentials
are supplied at runtime. The original `service_analyzer/` source contains
embedded credentials and stays ignored. This desktop CI does not run the
service tests yet; they need Linux images with the model dependencies. Add a
Linux job for them before adding a deploy workflow.

Recommended future GitHub Actions delivery flow:

```text
Reviewed service commit
  -> service tests + image build
  -> ECR image tagged with commit SHA; record immutable image digest
  -> staging EC2 deployment via Systems Manager
  -> /healthz + synthetic analyze/report smoke tests with staging RDS/S3
  -> explicit production promotion of the tested digest
  -> /healthz + smoke checks; restore previous image if checks fail
```

Use GitHub OIDC to assume a scoped AWS deployment role, restricted to this
repository and the intended branch or environment. Grant `id-token: write`
only to deployment jobs. Keep AWS access keys out of GitHub secrets. Let the
EC2 instance role read the image and runtime configuration from ECR and
Secrets Manager or Parameter Store. Keep production DB/HF/Ollama credentials
out of images and test jobs.

For a single EC2 instance, Systems Manager Run Command is a reasonable first
deployment mechanism: it avoids a GitHub runner needing an inbound SSH rule.
Track the command until completion and fail the job if the command or health
checks fail. Serialize production deployments. Account for in-progress
analysis/report tasks before replacing the container, and keep the previous
image digest for rollback. A single-instance replacement can interrupt the
service; use a load balancer and multiple instances when releases must avoid
downtime. AWS CodePipeline is optional; using AWS services does not require
moving this GitHub Actions pipeline into AWS.

Keep RDS private to the deployment network for migration jobs. Run those jobs
from within the VPC (for example, a controlled runner or Systems Manager
command on a managed node), and use a separate migration role/database user.
Use backward-compatible migrations so an older application image can still
run after rollback; restoring an image does not undo a database migration.
Do not run the rebuild/test schema against production. Take an appropriate
backup before destructive migrations and verify required EXECUTE grants.

Configure a GitHub `production` environment and restrict allowed release
branches/tags. Add required reviewers where the repository plan supports
them; otherwise keep production promotion as a restricted, explicit manual
workflow and limit repository write access. Separate staging and production
roles, EC2 targets, databases, and buckets or data prefixes.

References:

- [uv in GitHub Actions](https://docs.astral.sh/uv/guides/integration/github/)
- [GitHub-hosted runner capabilities](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)
- [GitHub OIDC with AWS](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws)
- [GitHub deployment environments](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments)
- [AWS Systems Manager Run Command](https://docs.aws.amazon.com/systems-manager/latest/userguide/run-command.html)
