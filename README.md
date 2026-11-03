# ADAM — Multimodal Service Satisfaction Analysis

ADAM records customer–staff interactions and brings video, voice, and text analysis into a desktop workspace for reviewing service quality, publishing reports, and coaching staff.

Built with **Python 3.13, PySide6/QML, and Briefcase**. The application interface is in English; development currently targets **macOS**.

[App tutorial](#app-tutorial) · [Getting started](#getting-started) · [Architecture](#architecture) · [Configuration](#configuration) · [Development and tests](#development-and-tests)

## App tutorial

The screenshots capture the current application with **sample accounts, scores, reports, and recordings**. They demonstrate the interface, not production analysis results. See the [workflow overview](assets/readme/user-workflow.png) for the complete service lifecycle.

**Sign in.** Use the ADAM username and password supplied by your administrator. Level `1` opens the staff workspace; levels `2–5` open the administrator workspace.

![ADAM sign-in screen](assets/readme/screenshots/login.png)

### Staff: record and review

**Home.** Compare **Customer** and **Staff** scores, choose a time period, and inspect **Recent Performance** or **Score Trend**. Select a performance bar to open that service, or **New Recording** to begin.

![Staff Home with sample scores and recent performance](assets/readme/screenshots/staff-home.png)

**Record.** Choose distinct **Customer camera** and **Staff camera** devices and a **Microphone**. Use **Confirm Devices** if the saved setup changed. Select **Open Recorder**, then **Start Recording** and **Stop Recording** in the separate window. The **Saving** card tracks uploads; failed saves retain the recordings and offer **Retry**.

![Record page with sample camera and microphone choices](assets/readme/screenshots/record.png)

![Recorder window with sample camera previews](assets/readme/screenshots/recorder.png)

**Analysis.** Select **Analyze** under **Not analyzed**. The service moves through **Analyzing** to **Completed**; waiting for a worker also counts as Analyzing. Use **Retry** after a failure or **View** to open completed results.

![Analysis status tabs and service actions](assets/readme/screenshots/analysis.png)

**Services.** Filter completed services by **From** / **To** dates, then **Search**; blank dates leave the range open. Select **Details** to compare scores, play synchronized media in **Videos**, and inspect **Charts**. The header search opens a service by ID. Staff can open only their own services.

![Staff service list with date filters](assets/readme/screenshots/staff-services.png)

![Service Details with sample scores, outcome, and synchronized video players](assets/readme/screenshots/service-detail.png)

**Reports.** Select a published report to preview its PDF and turn its pages, or choose **Open in Default App**. Only reports an administrator has published for you appear here.

![Staff Reports with a sample published PDF](assets/readme/screenshots/staff-reports.png)

**My Actions.** Open a goal, watch its example or marked moments, and reply in **Conversation**. **Got It** acknowledges the goal; the administrator completes it after reviewing a later service.

![My Actions with a sample goal and marked moments](assets/readme/screenshots/my-actions.png)

### Administrators: review, coach, and publish

**Home.** Review organization scores and trends, then compare employees in **Staff Ranking**. Switch **Customer** / **Staff** to choose which side's scores to compare.

![Administrator Home with sample organization scores and staff ranking](assets/readme/screenshots/admin-home.png)

**Services and feedback.** Filter by **Staff** and date, then open **Details**. Optionally pause the video, select **Add Moment**, write a note, and **Save Moment**. Set a **Goal**, explain **Why**, choose **Follow up by**, and **Send Goal to …**. The staff member receives it in My Actions.

![Administrator Services with staff and date filters](assets/readme/screenshots/admin-services.png)

![Administrator Service Details with the coaching panel beside the players](assets/readme/screenshots/admin-service-detail.png)

**Coaching.** Follow the selected goal's **Next step**. Choose a later analyzed service from the same employee with **Use as Follow-up**, then **Watch** it. **Mark Complete** requires a conclusion; **Keep Practicing** sets the next goal/date. Use **Check Again** while waiting for a service. **Edit Goal** and **Cancel Goal** retain the history.

![Coaching with sample follow-up choices and conversation](assets/readme/screenshots/coaching.png)

**Reports.** Select **New Report**, choose **Service**, **Staff**, or **Company**, select the subject or date range, and **Generate Report**. Preview before **Publish**. Published service/staff reports appear for that employee; company reports stay administrator-only. Period dates include both endpoints and use Pacific Time.

![Administrator Reports with a PDF ready for publication](assets/readme/screenshots/admin-reports.png)

![New Report dialog with scope and subject choices](assets/readme/screenshots/report-generator.png)

**Add Account.** Fill in **Username**, **Password**, **Name**, **Level**, and optional **Job Title**, then **Add Account**. Level `1` is staff; levels `2–5` are administrators. You can create accounts up to your own level.

![Add Account form and account-level guidance](assets/readme/screenshots/add-account.png)

### Both roles: Settings

Update your photo, name, username, or password in **Account**. Account changes use **Save**; **General** preferences apply immediately. Staff also have **Devices** and analysis-alert preferences. In **Help**, use **Check Connection** or **Clear Download Cache**; cache cleanup preserves original recordings and unfinished uploads.

![Settings with sample account information and preferences](assets/readme/screenshots/settings.png)

Use **Log Out** to return to sign-in or **Quit** to close ADAM. Both ask for confirmation; read the upload warning if a save is still running.

## Getting started

**This repository contains the desktop client and the analysis/report service.** A working installation also requires initialized MySQL tables/procedures/grants, S3 storage, and the analysis/report API with its SQS worker deployed separately (on EC2). The service is in [services/analyzer/](services/analyzer/) (real models); [services/analysis_service/](services/analysis_service/) is a lightweight fake with the same API and queue for local end-to-end tests. Database migrations and their runner are in [services/db/](services/db/), and the script that deploys the service on EC2 is in [deploy/](deploy/); GitHub Actions builds, tests, and deploys them ([CI notes](.github/CI.md#cd-for-the-analysis-service)). Design notes exist only in an ignored local directory (`docs/rebuild/`) and are not provided by a fresh clone. The [test schema](myapp/tests/fixtures/schema.sql) supports automated tests and is not a production migration.

### 1. Install dependencies

Install Homebrew, Xcode Command Line Tools, and uv, then run from the repository root:

```bash
brew install uv
cd myapp
uv python install 3.13
uv sync --locked
```

Dependencies are declared in [pyproject.toml](myapp/pyproject.toml) and pinned in [uv.lock](myapp/uv.lock). Every dependency installs from a prebuilt wheel; the microphone library (sounddevice) bundles PortAudio, so Homebrew's `portaudio` isn't needed.

### 2. Configure the desktop

Create `.env` in the repository root or `myapp/` (the packaged app reads `~/Library/Application Support/ADAM/.env` instead; see [Package a macOS app](#4-package-a-macos-app)), replacing the placeholders:

```dotenv
DB_HOST=<mysql-host>
DB_PORT=3306
DB_NAME=adam
DB_USER=adam_app
DB_PASSWORD=<database-password>
# Required for verified TLS when connecting to RDS.
DB_SSL_CA=/absolute/path/to/global-bundle.pem

AWS_REGION=us-east-1
AWS_PROFILE=<configured-local-profile>
S3_BUCKET=<private-media-bucket>
ANALYZE_API_URL=https://<analysis-host>/analyze
```

Use an existing ADAM login account from that database. ADAM accounts, MySQL users, and AWS credentials are separate. boto3 can use its standard credential sources if `AWS_PROFILE` is omitted.

For the SQS backend upgrade, apply **migration 007 → API and worker → desktop** in that order. The new desktop relies on the API to save analysis status `2` before responding; it is incompatible with a backend that still expects the desktop to write that status.

### 3. Launch

Run inside `myapp/`:

```bash
uv run briefcase dev
```

After changing dependencies, use `uv run briefcase dev -r`. Launch through Briefcase, which supplies the application metadata required by the entry point.

### 4. Package a macOS app

Run inside `myapp/` (Xcode Command Line Tools required):

```bash
uv run briefcase create
uv run briefcase build
uv run briefcase run
```

This builds `build/myapp/macos/app/ADAM.app` for Apple Silicon (macOS 14 or later) with an ad-hoc signature, which runs on the Mac that built it; distributing to other Macs needs Developer ID signing and notarization. The app never contains `.env`: each Mac keeps its own copy at `~/Library/Application Support/ADAM/.env` (`mkdir -p ~/Library/Application\ Support/ADAM`, then copy the file and `chmod 600` it). Settings already set in the environment take precedence. Without required database settings, the login screen says where the file belongs. Use an absolute `DB_SSL_CA` path, because an app opened from Finder starts in `/`.

To install the app on other Macs (first launch, settings file, camera and microphone access, updates, supported scope), follow [INSTALL.md](INSTALL.md).

## Architecture

![ADAM architecture: desktop client, SQS analysis queue, EC2 API and worker, S3, RDS, and optional Ollama Cloud](assets/readme/architecture.png)

The desktop connects directly to **RDS for MySQL 8.4** and a private **S3** bucket. **TaskRunner/DbLane** keep database, network, and PDF work off the UI thread. The analysis/report backend runs on **EC2**:

1. `POST /analyze` saves the job, outbox entry, and status `2` in one transaction, then returns `200`.
2. An outbox publisher sends the job to **SQS**. A separate worker claims a lease, processes the recordings, and renews its lease while running.
3. The worker uploads outputs, commits results and status `3`, then deletes the message. Duplicate deliveries cannot commit a second result; failed jobs are retried and can return to status `1` after exhausting attempts.
4. The desktop refreshes and watches database status. A successful submission remains accepted even if its immediate list refresh fails.

Service states are **1 · Not analyzed → 2 · Analyzing (queued or running) → 3 · Completed**.

As recorded on **November 2, 2026**, EC2 uses `adam-analyzer:2` in separate API and worker containers; `/healthz` includes `"sqs":"configured"`. The real analyzer passed desktop validation before the queue split; physical desktop validation of the SQS branch is still pending.

**Deployment:** a merge to `main` that changes the service, the migrations, or the deploy script runs [Deploy analyzer](.github/workflows/deploy-analyzer.yml). It builds the service and migration images, runs the tests inside them, and pushes the tested images to GitHub Container Registry. It then deploys them to EC2 through Systems Manager. On EC2, `deploy/deploy.sh` applies additive migrations, checks the database, and replaces the API and worker. It rolls back if the models are not ready within 5 minutes. A stopped instance deploys the recorded commit at its next boot. A manual run deploys any commit on `main`, which is also how to roll back.

### Analysis and reports

![Real analysis pipeline: speaker alignment, speech and emotion models, results, and reports](assets/readme/analysis-pipeline.png)

The real worker runs its models on the EC2 CPU with files loaded offline:

- **Speech:** pyannote speaker turns align faster-whisper `medium`/int8 words to staff and customer. OpenCC converts Chinese to Taiwan Traditional Chinese; opus-mt-zh-en translates it for VADER text sentiment.
- **Emotion:** Wav2Vec2 scores voice emotion; DeepFace scores each camera once per second.
- **Outputs:** RDS stores scores, state, and file references. S3 stores recordings, five charts, transcripts, report data, PDFs, and profile photos.

The API generates PDFs through `POST /reports`. Administrators preview before publishing service/staff reports to the relevant staff member. Optional **Ollama Cloud** adds report explanations and transcript-evidenced service outcomes/to-dos; unavailable AI does not block analysis or PDF generation. Report fields fall back to `Explanation unavailable`.

## Configuration

The `.env` example above covers required desktop settings. Additional options:

| Variable | Purpose |
| --- | --- |
| `DB_PORT` | MySQL port; defaults to `3306` |
| `DB_SSL_CA` | CA certificate path; enables certificate and hostname verification |
| `REPORTS_API_URL` | Override the report endpoint; otherwise replace the trailing `/analyze` in `ANALYZE_API_URL` with `/reports` |
| `AWS_ENDPOINT_URL` | Local AWS emulator endpoint, such as moto; remove for real AWS |
| `LOCAL_MEDIA_DIR`, `LOCAL_CHARTS_DIR` | Override download caches; defaults are under `~/Library/Caches/ADAM/` |
| `RECORDINGS_DIR` | Recordings folder (`~` allowed); defaults to `~/Movies/ADAM/`, with `video/` and `audio/` inside |

**Backend-only settings:** SQS queue/worker settings and `OLLAMA_BASE_URL`, `OLLAMA_API_KEY`, `OLLAMA_MODEL` belong in the backend environment, not the desktop `.env`. When AI is enabled, relevant scores, statistics, and translated transcript text are sent to Ollama Cloud.

Recordings remain in `~/Movies/ADAM/video/` and `~/Movies/ADAM/audio/` (earlier versions wrote `~/video/` and `~/audio/`; those files are left in place). Failed uploads preserve them for retry. **Settings → Help → Storage** shows both the recordings and download cache folders. **Settings → Help → Clear Download Cache** removes downloaded copies while retaining original recordings. Align desktop, database, and backend time settings: service times use desktop local time, while period reports use `America/Los_Angeles`; queue bookkeeping uses UTC.

## Development and tests

Run inside `myapp/`:

```bash
uv run pytest -m "not integration"  # Unit tests; no Docker needed
uv run pytest                       # Unit and integration tests
uv run pytest --cov                 # Branch coverage; 75% minimum
```

Integration tests use Qt offscreen, moto, and a disposable MySQL 8.4 container. Install Docker and make `mysql:8.4` available first. Missing integration prerequisites skip those tests; add `--require-integration` to require them. Tests do not load the development `.env`.

[GitHub Actions](.github/workflows/ci.yml) runs unit and integration tests on macOS with temporary native MySQL (`--mysql-backend=native`) and branch coverage, without AWS credentials. See [CI notes](.github/CI.md) for details.

The analysis services in `services/` have their own tests, which run inside Linux Docker images (their `uv.lock` files resolve for Linux, and the images include pytest). From `services/analyzer/` or `services/analysis_service/`:

```bash
docker build -t adam-service-test .
docker run --rm --network none -v "$PWD:/src:ro" -w /src adam-service-test python -m pytest -q -m "not integration"
```

Integration tests add a disposable MySQL 8.4 container loaded with the test schema and the service's `tests/integration/grants.sql`; see the [service test notes](services/analysis_service/README.md#測試). The two services share report, AI-explanation, outcome, and queue code byte for byte: after changing a shared file, copy it to the other service and run `python3 -I services/analysis_service/tools/check_report_copies.py` from the repository root.

The [Services workflow](.github/workflows/services.yml) runs these checks on Linux for every pull request: both service test suites inside their images, the shared-file comparison, the migration and deploy-script tests, shellcheck, and actionlint. Jobs whose files did not change finish in seconds. The [CI notes](.github/CI.md#service-checks) list the check names to require before merging to `main`.

| Path | Contents |
| --- | --- |
| `myapp/src/myapp/` | Python controllers, background work, storage, reports, and coaching |
| `myapp/src/myapp/qml/` | Staff/admin pages, shared components, icons, and theme |
| `myapp/tests/` | Unit tests, integration tests, and test fixtures |
| `services/analyzer/` | Analysis/report service: API, SQS worker, speech and emotion models, tests |
| `services/analysis_service/` | Fake analysis service with the same API and queue, for local end-to-end tests |
| `services/db/` | Database migrations and their runner, packaged as the `adam-db-migrations` image |
| `deploy/` | EC2 deploy script and the boot-time unit that deploys a pending commit |
| `.github/` | CI and deployment workflows, their scripts, and the [CI notes](.github/CI.md) |
| `assets/readme/` | README diagrams, screenshots, and image attribution |

Keep network/database work off the UI thread and statistics free of missing-score values (`-1`). Update `pyproject.toml` and `uv.lock` together when dependencies change; preserve existing CRLF line endings in QML files.

**Still to validate:** the SQS desktop flow against the deployed backend, Developer ID signing and notarization of the packaged app (an ad-hoc build passed a Finder-launched walkthrough on Apple Silicon against local services), Windows/Linux, and packaged system-notification banners. In-app notifications and Dock reminders are implemented.

Licensed under [BSD-3-Clause](myapp/LICENSE). See [README image sources](assets/readme/SOURCES.md) and [application asset licenses](myapp/src/myapp/images/SOURCES.md) for attribution.
