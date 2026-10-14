# Log Collector

Lightweight Windows log capture companion for MobaXterm, v0.1.0.

## How it works

1. In MobaXterm enable terminal logging for the session and verify its log file **is updating**.
2. Open Log Collector. Select the source log file corresponding to your target session (or browse the log folder).
3. Give the session a recognisable label (IP + tab/session name is recommended).
4. Click **Start**. The collector remembers the current end-of-file byte offset. Old data is excluded.
5. Click **Stop**. The newly appended bytes are saved as a separate `.log` file.

The capture is byte-exact and works with SSH and serial sessions **as long as MobaXterm writes them to a file**. The app does not use screen scraping or read terminal history. It cannot capture bytes that MobaXterm has not flushed to the source log. Do not treat identical IPs as unambiguous session identifiers: bind the exact file for each session.

The source log continues to grow in MobaXterm. This tool copies only the manually chosen interval. If the source file is replaced or truncated, the tool stops instead of silently mixing sessions. There is no automatic recovery from such events.

## Develop

```powershell
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m unittest discover -s tests -v
python app.py
```

## Windows build

On every push to `main` and manual workflow run, GitHub Actions tests and packages the single-file `LogCollector.exe` as an Actions artifact.

On the **first push to `main`**, GitHub Actions builds the executable, creates the matching tag `v0.1.0`, and publishes the EXE and checksum to GitHub Releases. For future releases, bump `VERSION` in `update.py` (e.g. `0.1.1`) and push `main` again. Existing release assets are deliberately not overwritten. If manually pushing a tag, it **must match `VERSION`**.

To push an empty repository from Windows, open PowerShell in this folder and run `./publish.ps1` (requires Git and authorized GitHub login). Alternatively use standard `git init / git add / git commit / git push` commands. Both approaches trigger the same build and release pipeline. Repository Actions must be enabled and allow the workflow's `contents: write` token permission.

The app checks for updates *only when clicked*. It verifies the downloaded SHA256 checksum. Updating replaces the current EXE after exit and leaves an `.exe.bak` backup. The EXE needs write permission to its installation directory; don't run it from a protected path like Program Files without an appropriate installer. Do not rename release assets. No update works in `python app.py` source mode.

### Known limitations

- Selecting the session is a manual log-file binding, not automatic introspection of MobaXterm tabs.
- Log lines buffered inside MobaXterm may appear later than the start/stop click.
- No Windows physical-machine or MobaXterm integration verification has yet been performed.
- Prefer keeping the log storage on a local disk rather than a network share; truncation / file replacement detection on unusual file systems might differ.
