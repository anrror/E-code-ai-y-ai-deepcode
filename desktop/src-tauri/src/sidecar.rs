use serde::Serialize;
use serde_json::{json, Value};
use std::collections::{HashMap, VecDeque};
use std::env;
use std::fmt;
use std::io::{BufRead, BufReader, Write};
#[cfg(debug_assertions)]
use std::path::Path;
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::mpsc::{self, SyncSender};
use std::sync::{Arc, Mutex, MutexGuard};
use std::thread;
use std::time::{Duration, Instant};
use tauri::{AppHandle, Emitter, Manager};

const PROTOCOL_VERSION: &str = "1.0";
const REQUEST_TIMEOUT: Duration = Duration::from_secs(30);
// Waited beyond the service's own budget, so its timeout message arrives first.
const RESPONSE_MARGIN: Duration = Duration::from_secs(5);
const STARTUP_TIMEOUT: Duration = Duration::from_secs(45);
const SHUTDOWN_REQUEST_TIMEOUT: Duration = Duration::from_secs(3);
const SHUTDOWN_EXIT_GRACE: Duration = Duration::from_secs(35);
const MAX_MESSAGE_BYTES: usize = 1024 * 1024;

type PendingResult = Result<Value, BridgeError>;

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BridgeError {
    pub code: String,
    pub message: String,
    pub retryable: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub data: Option<Value>,
}

impl BridgeError {
    pub(crate) fn new(
        code: impl Into<String>,
        message: impl Into<String>,
        retryable: bool,
    ) -> Self {
        Self {
            code: code.into(),
            message: message.into(),
            retryable,
            data: None,
        }
    }

    fn with_data(mut self, data: Value) -> Self {
        self.data = Some(data);
        self
    }
}

impl fmt::Display for BridgeError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "{}: {}", self.code, self.message)
    }
}

impl std::error::Error for BridgeError {}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SidecarStatus {
    pub phase: SidecarPhase,
    pub message: Option<String>,
    pub launch_source: Option<String>,
    pub server_info: Option<Value>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum SidecarPhase {
    Starting,
    Ready,
    Stopping,
    Stopped,
    Crashed,
}

impl Default for SidecarStatus {
    fn default() -> Self {
        Self {
            phase: SidecarPhase::Stopped,
            message: None,
            launch_source: None,
            server_info: None,
        }
    }
}

struct SidecarProcess {
    child: Child,
    stdin: Arc<Mutex<ChildStdin>>,
}

struct LaunchSpec {
    program: PathBuf,
    args: Vec<String>,
    current_dir: Option<PathBuf>,
    source: String,
}

pub struct RpcBridge {
    app: AppHandle,
    process: Mutex<Option<SidecarProcess>>,
    pending: Mutex<HashMap<u64, SyncSender<PendingResult>>>,
    next_id: AtomicU64,
    generation: AtomicU64,
    status: Mutex<SidecarStatus>,
    // Last stderr lines, kept for the crash report the UI shows.
    stderr_tail: Mutex<VecDeque<String>>,
}

impl RpcBridge {
    pub fn new(app: AppHandle) -> Self {
        Self {
            app,
            process: Mutex::new(None),
            pending: Mutex::new(HashMap::new()),
            next_id: AtomicU64::new(1),
            generation: AtomicU64::new(0),
            status: Mutex::new(SidecarStatus::default()),
            stderr_tail: Mutex::new(VecDeque::new()),
        }
    }

    pub fn status(&self) -> SidecarStatus {
        self.lock_status().clone()
    }

    pub fn start(self: &Arc<Self>) -> Result<SidecarStatus, BridgeError> {
        self.prepare_for_start()?;
        let generation = self.generation.fetch_add(1, Ordering::AcqRel) + 1;
        self.update_status(SidecarStatus {
            phase: SidecarPhase::Starting,
            message: None,
            launch_source: None,
            server_info: None,
        });

        let launch = match resolve_launch_spec(&self.app) {
            Ok(launch) => launch,
            Err(error) => {
                self.mark_crashed(error.message.clone());
                return Err(error);
            }
        };
        let mut command = Command::new(&launch.program);
        command
            .args(&launch.args)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        // The App Server is a console-subsystem binary. Without this flag
        // Windows gives it its own console window and a second taskbar entry,
        // which looks like a separate application (it is only a worker).
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;

            const CREATE_NO_WINDOW: u32 = 0x0800_0000;
            command.creation_flags(CREATE_NO_WINDOW);
        }
        if let Some(current_dir) = &launch.current_dir {
            command.current_dir(current_dir);
        }
        let mut child = match command.spawn() {
            Ok(child) => child,
            Err(error) => {
                let bridge_error = BridgeError::new(
                    "SIDECAR_LAUNCH_FAILED",
                    format!("failed to launch {}: {error}", launch.program.display()),
                    true,
                );
                self.mark_crashed(bridge_error.message.clone());
                return Err(bridge_error);
            }
        };
        let (Some(stdin), Some(stdout), Some(stderr)) =
            (child.stdin.take(), child.stdout.take(), child.stderr.take())
        else {
            let _ = child.kill();
            let _ = child.wait();
            let error = BridgeError::new(
                "SIDECAR_PIPE_FAILED",
                "one or more App Server stdio pipes were unavailable",
                true,
            );
            self.mark_crashed(error.message.clone());
            return Err(error);
        };

        *self.lock_process() = Some(SidecarProcess {
            child,
            stdin: Arc::new(Mutex::new(stdin)),
        });
        {
            let mut status = self.lock_status();
            status.launch_source = Some(launch.source);
        }
        self.emit_status();

        self.spawn_stdout_reader(stdout, generation);
        self.spawn_stderr_reader(stderr);
        self.spawn_exit_monitor(generation);

        let handshake = self.request_with_timeout(
            "initialize",
            json!({
                "protocolVersion": PROTOCOL_VERSION,
                "clientInfo": {
                    "name": "deepcode-desktop-host",
                    "version": env!("CARGO_PKG_VERSION"),
                    "surface": "desktop"
                }
            }),
            STARTUP_TIMEOUT,
        );
        match handshake {
            Ok(server_info) => {
                self.update_status(SidecarStatus {
                    phase: SidecarPhase::Ready,
                    message: None,
                    launch_source: self.status().launch_source,
                    server_info: Some(server_info),
                });
                Ok(self.status())
            }
            Err(error) => {
                self.mark_crashed(error.message.clone());
                self.force_kill();
                Err(error)
            }
        }
    }

    pub fn request(&self, method: &str, params: Value) -> PendingResult {
        let timeout = request_timeout(self.lock_status().server_info.as_ref(), method);
        self.request_with_timeout(method, params, timeout)
    }

    pub fn restart(self: &Arc<Self>) -> Result<SidecarStatus, BridgeError> {
        self.shutdown();
        self.start()
    }

    pub fn shutdown(&self) {
        if self.lock_process().is_none() {
            self.update_phase(SidecarPhase::Stopped, None);
            return;
        }
        self.update_phase(SidecarPhase::Stopping, None);
        let _ = self.request_with_timeout("shutdown", json!({}), SHUTDOWN_REQUEST_TIMEOUT);

        // The child is a native RPC attachment. Shutdown closes its connection;
        // allow pipe and transport cleanup to finish before reaping the relay.
        // The shared service retains the application and accepted tasks.
        let deadline = Instant::now() + SHUTDOWN_EXIT_GRACE;
        loop {
            let exited = {
                let mut process_slot = self.lock_process();
                match process_slot.as_mut() {
                    Some(process) => match process.child.try_wait() {
                        Ok(Some(_)) => {
                            process_slot.take();
                            true
                        }
                        Ok(None) => false,
                        Err(_) => false,
                    },
                    None => true,
                }
            };
            if exited || Instant::now() >= deadline {
                break;
            }
            thread::sleep(Duration::from_millis(25));
        }
        self.force_kill();
        self.fail_all(BridgeError::new(
            "SIDECAR_STOPPED",
            "the App Server has stopped",
            true,
        ));
        self.update_status(SidecarStatus {
            phase: SidecarPhase::Stopped,
            message: None,
            launch_source: self.status().launch_source,
            server_info: None,
        });
    }

    fn prepare_for_start(&self) -> Result<(), BridgeError> {
        if let Ok(mut tail) = self.stderr_tail.lock() {
            tail.clear();
        }
        let mut process = self.lock_process();
        if let Some(active) = process.as_mut() {
            match active.child.try_wait() {
                Ok(Some(_)) => {
                    process.take();
                }
                Ok(None) => {
                    return Err(BridgeError::new(
                        "SIDECAR_ALREADY_RUNNING",
                        "the App Server is already running",
                        false,
                    ));
                }
                Err(error) => {
                    return Err(BridgeError::new(
                        "SIDECAR_STATE_FAILED",
                        format!("could not inspect the App Server process: {error}"),
                        true,
                    ));
                }
            }
        }
        Ok(())
    }

    fn request_with_timeout(
        &self,
        method: &str,
        params: Value,
        timeout: Duration,
    ) -> PendingResult {
        let request_id = self.next_id.fetch_add(1, Ordering::Relaxed);
        let (sender, receiver) = mpsc::sync_channel(1);
        self.lock_pending().insert(request_id, sender);

        let message = json!({
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        });
        let encoded = match serde_json::to_vec(&message) {
            Ok(encoded) => encoded,
            Err(error) => {
                self.lock_pending().remove(&request_id);
                return Err(BridgeError::new(
                    "RPC_ENCODE_FAILED",
                    format!("could not encode request: {error}"),
                    false,
                ));
            }
        };
        if encoded.len() > MAX_MESSAGE_BYTES {
            self.lock_pending().remove(&request_id);
            return Err(BridgeError::new(
                "RPC_MESSAGE_TOO_LARGE",
                "request exceeds the App Server message limit",
                false,
            ));
        }

        let stdin = {
            let process = self.lock_process();
            process.as_ref().map(|process| Arc::clone(&process.stdin))
        };
        let Some(stdin) = stdin else {
            self.lock_pending().remove(&request_id);
            return Err(BridgeError::new(
                "SIDECAR_UNAVAILABLE",
                "the App Server is not running",
                true,
            ));
        };
        let write_result = {
            let mut writer = stdin
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner());
            writer
                .write_all(&encoded)
                .and_then(|_| writer.write_all(b"\n"))
                .and_then(|_| writer.flush())
        };
        if let Err(error) = write_result {
            self.lock_pending().remove(&request_id);
            return Err(BridgeError::new(
                "SIDECAR_WRITE_FAILED",
                format!("could not write to the App Server: {error}"),
                true,
            ));
        }

        match receiver.recv_timeout(timeout) {
            Ok(result) => result,
            Err(mpsc::RecvTimeoutError::Timeout) => {
                self.lock_pending().remove(&request_id);
                Err(BridgeError::new(
                    "RPC_TIMEOUT",
                    format!("{method} did not respond before the timeout"),
                    true,
                ))
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => Err(BridgeError::new(
                "SIDECAR_DISCONNECTED",
                "the App Server response channel closed",
                true,
            )),
        }
    }

    fn spawn_stdout_reader<R>(self: &Arc<Self>, stdout: R, generation: u64)
    where
        R: std::io::Read + Send + 'static,
    {
        let bridge = Arc::clone(self);
        thread::Builder::new()
            .name("deepcode-sidecar-stdout".into())
            .spawn(move || {
                let mut reader = BufReader::new(stdout);
                loop {
                    let mut line = Vec::new();
                    match reader.read_until(b'\n', &mut line) {
                        Ok(0) => break,
                        Ok(_) if line.len() > MAX_MESSAGE_BYTES => {
                            bridge.mark_crashed(
                                "App Server emitted a message above the size limit".into(),
                            );
                            break;
                        }
                        Ok(_) if bridge.is_current_generation(generation) => {
                            bridge.handle_stdout_message(&line)
                        }
                        Ok(_) => break,
                        Err(error) => {
                            bridge
                                .mark_crashed(format!("could not read App Server output: {error}"));
                            break;
                        }
                    }
                }
                bridge.handle_stdout_closed(generation);
            })
            .expect("failed to start App Server stdout reader");
    }

    fn spawn_stderr_reader<R>(self: &Arc<Self>, stderr: R)
    where
        R: std::io::Read + Send + 'static,
    {
        let app = self.app.clone();
        let bridge = self.clone();
        thread::Builder::new()
            .name("deepcode-sidecar-stderr".into())
            .spawn(move || {
                for line in BufReader::new(stderr).lines() {
                    match line {
                        Ok(message) => {
                            if let Ok(mut tail) = bridge.stderr_tail.lock() {
                                if tail.len() >= 40 {
                                    tail.pop_front();
                                }
                                tail.push_back(message.clone());
                            }
                            let _ = app.emit("deepcode://sidecar-log", message);
                        }
                        Err(_) => break,
                    }
                }
            })
            .expect("failed to start App Server stderr reader");
    }

    fn handle_stdout_message(&self, raw: &[u8]) {
        let message: Value = match serde_json::from_slice(raw) {
            Ok(message) => message,
            Err(error) => {
                let _ = self.app.emit(
                    "deepcode://sidecar-log",
                    format!("ignored malformed App Server message: {error}"),
                );
                return;
            }
        };
        if let Some(request_id) = message.get("id").and_then(Value::as_u64) {
            let sender = self.lock_pending().remove(&request_id);
            if let Some(sender) = sender {
                let result = response_result(&message);
                let _ = sender.send(result);
            }
            return;
        }
        if message.get("method").and_then(Value::as_str).is_some() {
            let _ = self.app.emit("deepcode://rpc-notification", message);
        }
    }

    fn handle_stdout_closed(&self, generation: u64) {
        if !self.is_current_generation(generation) {
            return;
        }
        let phase = self.status().phase;
        self.fail_all(BridgeError::new(
            "SIDECAR_DISCONNECTED",
            "the App Server output stream closed",
            true,
        ));
        match phase {
            SidecarPhase::Stopping | SidecarPhase::Stopped => {
                self.update_phase(SidecarPhase::Stopped, None);
            }
            SidecarPhase::Crashed => {}
            SidecarPhase::Starting | SidecarPhase::Ready => {
                self.mark_crashed("the App Server exited unexpectedly".into());
            }
        }
    }

    fn fail_all(&self, error: BridgeError) {
        let pending = std::mem::take(&mut *self.lock_pending());
        for (_, sender) in pending {
            let _ = sender.send(Err(error.clone()));
        }
    }

    fn force_kill(&self) {
        if let Some(mut process) = self.lock_process().take() {
            // The embedded App Server may have spawned helper processes
            // (--run-script for sandboxing and code mode). Killing only the
            // direct child would leave them holding the workspace.
            #[cfg(windows)]
            kill_process_tree(process.child.id());
            let _ = process.child.kill();
            let _ = process.child.wait();
        }
    }

    fn mark_crashed(&self, message: String) {
        self.update_status(SidecarStatus {
            phase: SidecarPhase::Crashed,
            message: Some(message),
            launch_source: self.status().launch_source,
            server_info: self.status().server_info,
        });
    }

    /// Watch the child for the whole Session. A dead App Server must surface as
    /// `crashed` with its exit code and last stderr lines, not as a client that
    /// silently stops answering requests.
    fn spawn_exit_monitor(self: &Arc<Self>, generation: u64) {
        let bridge = Arc::downgrade(self);
        thread::Builder::new()
            .name("deepcode-sidecar-monitor".into())
            .spawn(move || loop {
                thread::sleep(Duration::from_millis(500));
                let Some(bridge) = bridge.upgrade() else {
                    return;
                };
                let exited = {
                    let mut process = bridge.lock_process();
                    match process.as_mut() {
                        Some(active) => match active.child.try_wait() {
                            Ok(Some(status)) => Some(status.code()),
                            Ok(None) => None,
                            Err(_) => return,
                        },
                        None => return,
                    }
                };
                if let Some(code) = exited {
                    bridge.mark_exited(code, generation);
                    return;
                }
            })
            .expect("failed to start App Server monitor");
    }

    fn mark_exited(&self, code: Option<i32>, generation: u64) {
        if self.generation.load(Ordering::Acquire) != generation {
            return;
        }
        if matches!(
            self.status().phase,
            SidecarPhase::Stopping | SidecarPhase::Stopped
        ) {
            return;
        }
        let tail = match self.stderr_tail.lock() {
            Ok(lines) => lines
                .iter()
                .rev()
                .take(5)
                .cloned()
                .collect::<Vec<_>>()
                .into_iter()
                .rev()
                .collect::<Vec<_>>()
                .join("\n"),
            Err(poisoned) => poisoned.into_inner().iter().cloned().collect::<Vec<_>>().join("\n"),
        };
        let mut message = match code {
            Some(code) => format!("the App Server exited unexpectedly (exit code {code})"),
            None => "the App Server exited unexpectedly".to_string(),
        };
        if !tail.is_empty() {
            message.push_str(":\n");
            message.push_str(&tail);
        }
        self.mark_crashed(message);
    }

    fn update_phase(&self, phase: SidecarPhase, message: Option<String>) {
        let previous = self.status();
        self.update_status(SidecarStatus {
            phase,
            message,
            launch_source: previous.launch_source,
            server_info: previous.server_info,
        });
    }

    fn update_status(&self, status: SidecarStatus) {
        *self.lock_status() = status;
        self.emit_status();
    }

    fn emit_status(&self) {
        let _ = self.app.emit("deepcode://sidecar-state", self.status());
    }

    fn lock_process(&self) -> MutexGuard<'_, Option<SidecarProcess>> {
        self.process
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
    }

    fn lock_pending(&self) -> MutexGuard<'_, HashMap<u64, SyncSender<PendingResult>>> {
        self.pending
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
    }

    fn lock_status(&self) -> MutexGuard<'_, SidecarStatus> {
        self.status
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
    }

    fn is_current_generation(&self, generation: u64) -> bool {
        self.generation.load(Ordering::Acquire) == generation
    }
}

fn response_result(message: &Value) -> PendingResult {
    if let Some(result) = message.get("result") {
        return Ok(result.clone());
    }
    let error = message.get("error").ok_or_else(|| {
        BridgeError::new(
            "INVALID_RPC_RESPONSE",
            "App Server response contained neither result nor error",
            false,
        )
    })?;
    let data = error.get("data").cloned().unwrap_or(Value::Null);
    let stable_code = data
        .get("code")
        .and_then(Value::as_str)
        .unwrap_or("APP_SERVER_ERROR");
    let message = error
        .get("message")
        .and_then(Value::as_str)
        .unwrap_or("App Server request failed");
    let retryable = data
        .get("retryable")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    Err(BridgeError::new(stable_code, message, retryable).with_data(data))
}

fn resolve_launch_spec(app: &AppHandle) -> Result<LaunchSpec, BridgeError> {
    if let Some(configured) = env::var_os("DEEPCODE_APP_SERVER_PATH") {
        let path = PathBuf::from(configured);
        if path.is_file() {
            return Ok(binary_launch(path, "DEEPCODE_APP_SERVER_PATH"));
        }
        return Err(BridgeError::new(
            "SIDECAR_NOT_FOUND",
            format!(
                "DEEPCODE_APP_SERVER_PATH does not point to a file: {}",
                path.display()
            ),
            false,
        ));
    }

    let binary_name = if cfg!(windows) {
        "deepcode-app-server.exe".to_owned()
    } else {
        "deepcode-app-server".to_owned()
    };

    #[cfg(debug_assertions)]
    let repository = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    #[cfg(debug_assertions)]
    if let Some(launch) = source_virtualenv_launch(&repository) {
        return Ok(launch);
    }

    if let Ok(resource_dir) = app.path().resource_dir() {
        let resource_binary = resource_dir.join("app-server").join(&binary_name);
        if resource_binary.is_file() {
            return Ok(binary_launch(
                resource_binary,
                "bundled App Server resource",
            ));
        }
    }

    #[cfg(debug_assertions)]
    {
        let source_bundle = repository
            .join("desktop/build/sidecar/dist/deepcode-app-server")
            .join(&binary_name);
        if source_bundle.is_file() {
            return Ok(binary_launch(source_bundle, "source App Server bundle"));
        }
    }

    #[cfg(debug_assertions)]
    const MISSING_SIDECAR_MESSAGE: &str =
        "could not find a bundled App Server or a source development runtime";
    #[cfg(not(debug_assertions))]
    const MISSING_SIDECAR_MESSAGE: &str = "the bundled App Server resource is missing";
    Err(BridgeError::new(
        "SIDECAR_NOT_FOUND",
        MISSING_SIDECAR_MESSAGE,
        false,
    ))
}

#[cfg(debug_assertions)]
fn source_virtualenv_launch(repository: &Path) -> Option<LaunchSpec> {
    if !repository.join("app_server/__main__.py").is_file() {
        return None;
    }
    let virtualenv_python = if cfg!(windows) {
        repository.join(".venv/Scripts/python.exe")
    } else {
        repository.join(".venv/bin/python")
    };
    virtualenv_python.is_file().then(|| LaunchSpec {
        program: virtualenv_python,
        args: [
            vec!["-m".into(), "app_server".into(), "--embedded".into()],
            database_args(),
        ]
        .concat(),
        current_dir: Some(repository.to_path_buf()),
        source: "source virtualenv".into(),
    })
}

fn binary_launch(program: PathBuf, source: &str) -> LaunchSpec {
    LaunchSpec {
        program,
        args: [vec!["--embedded".into()], database_args()].concat(),
        current_dir: None,
        source: source.into(),
    }
}

fn database_args() -> Vec<String> {
    env::var_os("DEEPCODE_DATABASE_PATH")
        .filter(|value| !value.is_empty())
        .map(|value| {
            vec![
                "--database".into(),
                PathBuf::from(value).to_string_lossy().into_owned(),
            ]
        })
        .unwrap_or_default()
}

/// Windows keeps grandchild processes alive when only the direct child dies.
/// The App Server runs helper scripts, so tear the whole tree down.
#[cfg(windows)]
fn kill_process_tree(pid: u32) {
    use std::os::windows::process::CommandExt;

    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    let _ = Command::new("taskkill")
        .args(["/PID", &pid.to_string(), "/T", "/F"])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .creation_flags(CREATE_NO_WINDOW)
        .status();
}

/// How long to wait for `method`, from the budgets the service sent in its
/// `initialize` reply. A service too old to send them gets the old waits.
fn request_timeout(server_info: Option<&Value>, method: &str) -> Duration {
    let budgets = server_info.and_then(|info| info.pointer("/capabilities/requestTimeouts"));
    let seconds = budgets.and_then(|budgets| {
        budgets
            .get("methodSeconds")
            .and_then(|methods| methods.get(method))
            .or_else(|| budgets.get("defaultSeconds"))
            .and_then(Value::as_f64)
    });
    match seconds {
        Some(seconds) if seconds > 0.0 && seconds <= 3600.0 => {
            Duration::from_secs_f64(seconds) + RESPONSE_MARGIN
        }
        _ if method == "provider/test" => Duration::from_secs(125),
        _ => REQUEST_TIMEOUT,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn waits_as_long_as_the_service_allows_each_method() {
        let info = json!({"capabilities": {"requestTimeouts": {
            "defaultSeconds": 25.0,
            "methodSeconds": {"thread/context/compact": 600.0}
        }}});
        assert_eq!(
            request_timeout(Some(&info), "thread/context/compact"),
            Duration::from_secs(605)
        );
        assert_eq!(request_timeout(Some(&info), "thread/list"), REQUEST_TIMEOUT);

        let older = json!({"capabilities": {}});
        assert_eq!(
            request_timeout(Some(&older), "provider/test"),
            Duration::from_secs(125)
        );
        assert_eq!(
            request_timeout(Some(&older), "thread/context/compact"),
            REQUEST_TIMEOUT
        );
        assert_eq!(request_timeout(None, "thread/list"), REQUEST_TIMEOUT);
    }

    #[test]
    fn extracts_stable_application_error() {
        let result = response_result(&json!({
            "jsonrpc": "2.0",
            "id": 7,
            "error": {
                "code": -32000,
                "message": "project is missing",
                "data": {"code": "NOT_FOUND", "retryable": false}
            }
        }));
        let error = result.unwrap_err();
        assert_eq!(error.code, "NOT_FOUND");
        assert_eq!(error.message, "project is missing");
        assert!(!error.retryable);
    }

    #[test]
    fn rejects_malformed_response() {
        let error = response_result(&json!({"jsonrpc": "2.0", "id": 1})).unwrap_err();
        assert_eq!(error.code, "INVALID_RPC_RESPONSE");
    }
}
