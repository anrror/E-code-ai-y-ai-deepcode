# Troubleshooting

Find the problem that matches what you are seeing below. When asking for help,
include your DFcode version, operating system, the command you ran, and the
error message. Remove API keys and access links from anything you share.

## My terminal cannot find `dfcode`

If you installed with `uv tool`, run:

```console
uv tool update-shell
```

Close and reopen your terminal, then check the installation:

```console
dfcode --version
```

If the command is still missing, follow the [installation steps](../../README.md#quick-start).

## DFcode asks me to trust my project

For a project you trust, start the TUI with its folder path:

```console
dfcode --workspace /path/to/your-project --trust
```

Replace the path with your project directory. In Desktop/Web, open the Project
and use its trust control. You only need to record trust once per project;
individual tool actions may still ask for approval.

## The wrong model is selected, or the model fails

Use `/model` in the TUI or the model picker beside the Desktop/Web message box
to choose the connection and model for your conversation.

To check a connection from your terminal, replace `CONNECTION_ID` and `MODEL_ID`
with your configured connection name and model ID:

```console
dfcode provider test CONNECTION_ID --model MODEL_ID
```

If a model can answer messages but fails to use tools, try the tool-calling check:

```console
dfcode provider test CONNECTION_ID --model MODEL_ID --agent
```

Use the reported error to check your API key, endpoint, and model settings.
See [Models and providers](models.md) for setup examples.

## The browser will not connect

If the page shows **Browser access required**, run `dfcode web` and open the
new link. This also works when you paste the link into the same browser tab.

For `APP_SERVER_OFFLINE` or another connection error, first check
whether the background service is running:

```console
dfcode service status
```

If it reports `ready`, open a fresh browser link:

```console
dfcode web
```

Use the link within 60 seconds. Each link works once; the resulting browser
session lasts up to 12 hours. Restarting the service also requires a fresh link.
You do not need a DFcode account.

If the service is stopped, start it and try again:

```console
dfcode service start
```

If startup fails, read the recent logs:

```console
dfcode service logs --lines 100
```

## I lost the connection while DFcode was working

In the TUI, enter `/reconnect`. In Desktop/Web, use **Reconnect**; if the browser
asks you to sign in, run `dfcode web` again.

Open your conversation and check its latest messages and pending approvals.
The task may have kept running while you were disconnected. Continue from its
current state rather than sending the original task again.

If you see `RESULT_UNKNOWN` after an action, check what changed before retrying:
open the file after an edit, check the setting after saving, or look for the task
in your conversation. For details, see [connection recovery](../WEB_CLIENT.md#reconnection-and-uncertain-results).

## Desktop will not open

Make sure you have installed the [Desktop app or its source dependencies](../../desktop/README.md).
For an app installed in a custom location, run:

```console
dfcode desktop --app /path/to/app
```

For a source checkout in another directory:

```console
dfcode desktop --source /path/to/DFcode
```

If the source app fails to build, check Node.js, Rust, and your platform's
prerequisites in the Desktop guide. Keep its launch terminal open while using it.

## I updated the source, but DFcode still uses the previous version

For a version upgrade that changes stored data, follow
[Upgrading DFcode](../UPGRADE_AND_RESTORE.md) to back up your installation first.
To update your source installation, rebuild and reinstall from the repository root:

```console
npm --prefix desktop ci
npm --prefix desktop run build:web
uv tool install --python 3.12 --force .
```

After current work finishes, restart the service:

```console
dfcode service restart --drain --timeout 60
```

Open a fresh `dfcode web` link if you use the browser. For source Desktop,
reopen with `dfcode desktop --setup` to refresh its bundled resources too.

If the restart times out, finish or stop the reported tasks and close any active
terminals, then try again.

## I need to recover saved conversations or settings

Follow the [backup and restore guide](../UPGRADE_AND_RESTORE.md). Back up your
project files too; restoring DFcode's data does not undo changes to your code.

Keep `~/.dfcode/state/dfcode.sqlite3` when troubleshooting. It stores tasks,
approvals, and Automations as well as conversation-related state. Deleting it
can lose information that cannot be recovered from conversation files alone.
