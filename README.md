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

**This repository contains the desktop client.** A working installation also requires initialized MySQL tables/procedures/grants, S3 storage, and the separately deployed analysis/report API with its SQS worker. Backend implementations, production migrations, and deployment notes exist only in ignored local directories (`docs/rebuild/analyzer/`, `docs/rebuild/analysis_service/`, and `docs/rebuild/`); they are not provided by a fresh clone. The [test schema](myapp/tests/fixtures/schema.sql) supports automated tests and is not a production migration.

### 1. Install dependencies

Install Homebrew, Xcode Command Line Tools, and these native dependencies, then run from the repository root:

```bash
brew install uv portaudio
cd myapp
uv python install 3.13
uv sync --locked
```

Dependencies are declared in [pyproject.toml](myapp/pyproject.toml) and pinned in [uv.lock](myapp/uv.lock). PyAudio compiles locally; if Xcode selects an incompatible beta SDK, set `SDKROOT` to a compatible installed macOS SDK before installing.

### 2. Configure the desktop

Create `.env` in the repository root or `myapp/`, replacing the placeholders:

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

## Architecture

![ADAM architecture: desktop client, SQS analysis queue, EC2 API and worker, S3, RDS, and optional Ollama Cloud](assets/readme/architecture.png)

The desktop connects directly to **RDS for MySQL 8.4** and a private **S3** bucket. **TaskRunner/DbLane** keep database, network, and PDF work off the UI thread. The analysis/report backend runs on **EC2**:

1. `POST /analyze` saves the job, outbox entry, and status `2` in one transaction, then returns `200`.
2. An outbox publisher sends the job to **SQS**. A separate worker claims a lease, processes the recordings, and renews its lease while running.
3. The worker uploads outputs, commits results and status `3`, then deletes the message. Duplicate deliveries cannot commit a second result; failed jobs are retried and can return to status `1` after exhausting attempts.
4. The desktop refreshes and watches database status. A successful submission remains accepted even if its immediate list refresh fails.

Service states are **1 · Not analyzed → 2 · Analyzing (queued or running) → 3 · Completed**.

As recorded on **November 2, 2026**, EC2 uses `adam-analyzer:2` in separate API and worker containers; `/healthz` includes `"sqs":"configured"`. The real analyzer passed desktop validation before the queue split; physical desktop validation of the SQS branch is still pending.

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
| `LOCAL_MEDIA_DIR`, `LOCAL_CHARTS_DIR` | Override download caches; defaults are under `src/myapp/init/` |

**Backend-only settings:** SQS queue/worker settings and `OLLAMA_BASE_URL`, `OLLAMA_API_KEY`, `OLLAMA_MODEL` belong in the backend environment, not the desktop `.env`. When AI is enabled, relevant scores, statistics, and translated transcript text are sent to Ollama Cloud.

Recordings normally remain in `~/video/` and `~/audio/` when launched with Briefcase. Failed uploads preserve them for retry. **Settings → Help → Clear Download Cache** removes downloaded copies while retaining original recordings. Align desktop, database, and backend time settings: service times use desktop local time, while period reports use `America/Los_Angeles`; queue bookkeeping uses UTC.

## Development and tests

Run inside `myapp/`:

```bash
uv run pytest -m "not integration"  # Unit tests; no Docker needed
uv run pytest                       # Unit and integration tests
uv run pytest --cov                 # Branch coverage; 75% minimum
```

Integration tests use Qt offscreen, moto, and a disposable MySQL 8.4 container. Install Docker and make `mysql:8.4` available first. Missing integration prerequisites skip those tests; add `--require-integration` to require them. Tests do not load the development `.env`.

[GitHub Actions](.github/workflows/ci.yml) runs unit and integration tests on macOS with temporary native MySQL (`--mysql-backend=native`) and branch coverage, without AWS credentials. See [CI notes](.github/CI.md) for details.

| Path | Contents |
| --- | --- |
| `myapp/src/myapp/` | Python controllers, background work, storage, reports, and coaching |
| `myapp/src/myapp/qml/` | Staff/admin pages, shared components, icons, and theme |
| `myapp/tests/` | Unit tests, integration tests, and test fixtures |
| `assets/readme/` | README diagrams, screenshots, and image attribution |

Keep network/database work off the UI thread and statistics free of missing-score values (`-1`). Update `pyproject.toml` and `uv.lock` together when dependencies change; preserve existing CRLF line endings in QML files.

**Still to validate:** the SQS desktop flow on physical hardware, Windows/Linux, Briefcase build/package with the current uv setup, and packaged system-notification banners. In-app notifications and Dock reminders are implemented.

Licensed under [BSD-3-Clause](myapp/LICENSE). See [README image sources](assets/readme/SOURCES.md) and [application asset licenses](myapp/src/myapp/images/SOURCES.md) for attribution.
