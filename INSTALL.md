# Installing ADAM on a Mac

This guide walks you through installing the ADAM desktop app, connecting it to your organization's services, and checking that it works. It is written for the people who use ADAM every day and for the administrators who prepare their Macs.

To learn the app itself (recording, analysis, reports, coaching), see the [App tutorial](README.md#app-tutorial) in the README. To build the installer from source, see [Package a macOS app](README.md#4-package-a-macos-app).

**Contents:** [What you need](#1-what-you-need) · [Install](#2-install-the-app) · [First launch](#3-open-adam-for-the-first-time) · [Settings file](#4-add-the-settings-file) · [Sign in](#5-sign-in) · [Camera and microphone](#6-allow-the-camera-and-microphone) · [Check the setup](#7-check-the-setup) · [Where your files are](#8-where-adam-keeps-your-files) · [Update](#9-update-adam) · [Remove](#10-remove-adam) · [What this release supports](#11-what-this-release-supports) · [Troubleshooting](#12-troubleshooting) · [For administrators](#13-for-administrators)

## 1. What you need

| | |
| --- | --- |
| **Mac** | Apple silicon (M1 or later) with macOS 14 Sonoma or later |
| **Disk space** | About 750 MB for the app, plus room for recordings (roughly 15 MB per minute of recording) |
| **Cameras** (staff) | Two cameras: one facing the customer and one facing you, for example the built-in camera plus a USB camera, or an iPhone through Continuity Camera |
| **Microphone** (staff) | The built-in microphone or an external one |
| **From your administrator** | The installer (`ADAM-<version>.dmg`), a settings file named `.env`, and your ADAM username and password |
| **Network** | Access to your organization's ADAM services: the database, cloud storage, and the analysis service |

Administrator accounts review, coach and publish but don't record, so their Macs don't need cameras or a microphone.

## 2. Install the app

1. Double-click `ADAM-<version>.dmg`.
2. In the window that opens, drag **ADAM** onto **Applications**.
3. Eject the installer: in the Finder sidebar, click the eject button next to **ADAM**.

## 3. Open ADAM for the first time

Open **ADAM** from the Applications folder, Launchpad or Spotlight.

ADAM builds are signed for use within your organization rather than notarized by Apple, so macOS asks you to confirm the first launch of each new version:

1. If macOS says it can't verify ADAM or that ADAM was blocked, click **Done**.
2. Open **System Settings → Privacy & Security**, scroll to **Security**, and click **Open Anyway** next to the message about ADAM.
3. Confirm with **Open Anyway** and your Mac password if asked.

On macOS 14 you can instead Control-click ADAM in Applications, choose **Open**, then **Open** again.

ADAM starts with the **Welcome to ADAM** sign-in window.

## 4. Add the settings file

ADAM reads its connection settings from a file named `.env` in your Library folder. The file is not part of the app: every Mac keeps its own copy, so passwords and keys never travel inside the installer. Treat it like a password.

If the file is missing, the sign-in window says:

> Can't find ADAM's settings. Put the .env file from your administrator in ~/Library/Application Support/ADAM/, then try again.

Copy the file in **Terminal** (Finder hides names that start with a dot and won't let you rename a file to `.env`):

```bash
mkdir -p ~/Library/Application\ Support/ADAM
cp ~/Downloads/.env ~/Library/Application\ Support/ADAM/.env
chmod 600 ~/Library/Application\ Support/ADAM/.env
```

Replace `~/Downloads/.env` with the place where you saved the file, even if it has a different name. `chmod 600` makes it readable only by your user account.

You don't need to restart ADAM: once the file is in place, click **Log In**. Settings already set in the environment take precedence over the file.

The file holds these settings. Your administrator fills them in; you only need this list to check a file:

| Setting | Purpose |
| --- | --- |
| `DB_HOST`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` | The ADAM database (required) |
| `DB_PORT` | Database port, `3306` if omitted |
| `DB_SSL_CA` | Full path to the database's certificate bundle, for encrypted connections. Quote it when the path has spaces, for example `DB_SSL_CA="/Users/<you>/Library/Application Support/ADAM/global-bundle.pem"` |
| `S3_BUCKET`, `AWS_REGION` | Cloud storage for recordings, charts and reports |
| `AWS_PROFILE` or `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` | Storage credentials: a profile configured on this Mac, or keys in this file |
| `ANALYZE_API_URL` | The analysis service, ending in `/analyze` |
| `REPORTS_API_URL` | Optional: the report service, if it isn't next to the analysis service |
| `RECORDINGS_DIR` | Optional: where recordings are saved (`~` allowed), `~/Movies/ADAM` if omitted |
| `LOCAL_MEDIA_DIR`, `LOCAL_CHARTS_DIR` | Optional: where downloaded videos and charts are cached |

## 5. Sign in

Enter the username and password from your administrator and click **Log In**. Staff accounts open the staff workspace; administrator accounts open the administrator workspace.

| Message | What it means |
| --- | --- |
| Incorrect username or password. Try again. | Check the spelling and Caps Lock |
| Can't connect to the database. Check your network and try again. | The Mac can't reach the database: check Wi-Fi or VPN, then click **Log In** again |
| Can't log in because of a database problem. Contact your administrator. | The database answered but refused the settings: the settings file needs checking |

## 6. Allow the camera and microphone

*Staff accounts only.*

The first time you open **Record**, macOS asks whether ADAM may use the camera ("ADAM uses the camera to record service sessions."). The first recording asks for the microphone in the same way. Click **Allow** both times. If the Record page shows **No cameras found**, allow access, connect both cameras and click **Scan Again**.

If you clicked **Don't Allow**, open **System Settings → Privacy & Security → Camera** (or **Microphone**), turn on **ADAM**, and reopen ADAM when macOS asks.

To keep your usual devices, open **Settings → Devices → Set Up Devices**, choose the customer camera, the staff camera and the microphone, check both previews and the microphone level, and click **Save**. The Record page then starts from this setup.

## 7. Check the setup

1. Open **Settings**, scroll to **Help**, and click **Check Connection**. **Database**, **Cloud Storage** and **Analysis Service** should each show **Available**.
2. Staff accounts: make a short test recording. Open **Record**, select **Open Recorder**, then **Start Recording**, and stop after about ten seconds. The recorder shows **Saved** once the three files are uploaded; then click **Analyze**. ADAM shows **Analysis complete** when the results are ready.
3. Under **Help → Storage**, the **Recordings folder** and **Download cache folder** show where ADAM keeps files on this Mac.

If your administrator asks for diagnostics, click **Copy Diagnostic Summary** and paste it into your message. It contains version numbers and check results only: no passwords, addresses or customer data.

## 8. Where ADAM keeps your files

| What | Where | Notes |
| --- | --- | --- |
| Settings file | `~/Library/Application Support/ADAM/.env` | Kept when you update or remove the app |
| Recordings | `~/Movies/ADAM/video/` and `~/Movies/ADAM/audio/` | Change with `RECORDINGS_DIR`. Recordings stay on this Mac after upload |
| Download cache | `~/Library/Caches/ADAM/` | Copies of videos, charts and reports from cloud storage. Cleared at **Log Out**, **Quit** and **Settings → Help → Clear Download Cache**; recordings are never part of it |
| Preferences and unfinished uploads | `~/Library/Preferences/com.adam.myapp.plist` | Window mode, volume, device setup, and recordings still waiting to upload |

Recordings remain on your Mac after they are uploaded, so you always have your own copy. When you need space, remove older recordings from `~/Movies/ADAM`; their uploaded copies stay in cloud storage.

## 9. Update ADAM

1. Quit ADAM. If a recording is still uploading, ADAM asks before quitting; let the upload finish or retry it after the update.
2. Install the new version as in [step 2](#2-install-the-app) and choose **Replace**.
3. Confirm the first launch as in [step 3](#3-open-adam-for-the-first-time).

Your settings file, recordings, preferences and saved device setup stay in place. A recording that hadn't finished uploading is offered again with **Retry** the next time you sign in. Because each internal build is signed separately, macOS may ask for camera and microphone access again.

## 10. Remove ADAM

1. Quit ADAM and drag it from Applications to the Trash.
2. To remove its data as well, delete `~/Library/Application Support/ADAM`, `~/Library/Caches/ADAM` and `~/Library/Preferences/com.adam.myapp.plist`.
3. Recordings in `~/Movies/ADAM` are yours: back them up or delete them.
4. Optionally reset the camera and microphone permissions:

   ```bash
   tccutil reset Camera io.github.1104030360.myapp
   tccutil reset Microphone io.github.1104030360.myapp
   ```

## 11. What this release supports

- **Platform:** Macs with Apple silicon running macOS 14 or later. Intel Macs, Windows and Linux are outside this release.
- **Distribution:** builds are signed for internal distribution. Each new version needs the one-time confirmation in [step 3](#3-open-adam-for-the-first-time), and macOS may ask for camera and microphone access again after an update. Signing with an Apple Developer ID and notarizing the app would remove both.
- **Connected use:** signing in, uploading, analysis and viewing results use your organization's services. Recording doesn't depend on the network: recordings are saved on the Mac first, and an upload that couldn't finish can be retried later, even after ADAM restarts.
- **Configuration:** each Mac needs its own settings file. By design, the app carries no connection details or credentials.
- **Recording:** two cameras and a microphone on each staff Mac. Each camera records 640 × 480 video; audio is 16-bit mono at 44.1 kHz.
- **Analysis:** scores, charts, transcripts and reports come from the analysis service. How long they take depends on the length of the recording and the load on the service; ADAM lets you know when results are ready.
- **Interface:** English.
- **Notifications:** shown inside ADAM, with a bounce and badge on its Dock icon when ADAM is in the background.
- **Size:** about 720 MB once installed. ADAM includes its own Python and Qt runtime, so nothing else needs to be installed on the Mac.

## 12. Troubleshooting

| Problem | What to do |
| --- | --- |
| macOS says ADAM can't be opened or was blocked | Follow [step 3](#3-open-adam-for-the-first-time) |
| The sign-in window says it can't find ADAM's settings | Check that the file is named exactly `.env` (not `.env.txt`) and sits in `~/Library/Application Support/ADAM/`, then click **Log In** |
| Can't connect to the database | Check Wi-Fi or VPN, then **Check Connection** (step 7). If **Database** stays unavailable, contact your administrator |
| No cameras found | Allow camera access (step 6), connect both cameras and click **Scan Again**. For an iPhone camera, keep the iPhone near the Mac and signed in to the same Apple Account |
| The microphone level stays flat | Choose the microphone you speak into on the Record page or in **Settings → Devices**, and check **Privacy & Security → Microphone** |
| Results don't appear | Open **Analysis** to see the service's status, use **Retry** if it failed, and check **Analysis Service** under **Check Connection** |
| A recording wasn't saved | Its files stay on the Mac. Use **Retry** on the Record page's **Saving** card or in the notification |
| Low on disk space | Click **Clear Download Cache**, and remove older recordings from `~/Movies/ADAM` |

**Collecting a log for support.** Quit ADAM, then run:

```bash
open -n --stderr ~/Desktop/adam-log.txt /Applications/ADAM.app
```

Reproduce the problem, quit ADAM, and send `~/Desktop/adam-log.txt` to your administrator. The log can include service IDs and server addresses, so share it only with them.

## 13. For administrators

- **Installer:** build it with `uv run briefcase package --adhoc-sign` from `myapp/`, which produces `dist/ADAM-<version>.dmg` (see [Package a macOS app](README.md#4-package-a-macos-app)). Share it through your usual internal channel, such as a file share or device management.
- **Settings file:** prepare one per Mac with the settings in [step 4](#4-add-the-settings-file). Give the desktop app its own least-privilege database user and storage credentials, and use full paths, because ADAM opened from Finder starts in `/`. Put the database certificate bundle next to the settings file if you use `DB_SSL_CA`.
- **Accounts:** create staff and administrator accounts with **Add Account** in the administrator workspace. Staff accounts record; administrator accounts review, coach and publish.
- **Rollout check:** on a new Mac, run [step 7](#7-check-the-setup) once before handing it over.
- **Trusted distribution:** signing ADAM with an Apple Developer ID certificate and notarizing it lets users open it without the confirmation in step 3 and keeps camera and microphone permissions across updates.
