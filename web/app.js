const terminalElement = document.querySelector("#terminal");
const connectionPanel = document.querySelector("#connectionPanel");
const connectionToggleButton = document.querySelector("#connectionToggleButton");
const statusDot = document.querySelector("#statusDot");
const statusText = document.querySelector("#statusText");
const statusDetail = document.querySelector("#statusDetail");
const sessionLabel = document.querySelector("#sessionLabel");
const restartButton = document.querySelector("#restartButton");
const refreshButton = document.querySelector("#refreshButton");
const serverUrl = document.querySelector("#serverUrl");
const connectButton = document.querySelector("#connectButton");
const commandInput = document.querySelector("#commandInput");
const commandPanel = document.querySelector(".command-panel");
const commandRow = document.querySelector(".command-row");
const escButton = document.querySelector("#escButton");
const slashButton = document.querySelector("#slashButton");
const upButton = document.querySelector("#upButton");
const downButton = document.querySelector("#downButton");
const enterButton = document.querySelector("#enterButton");
const newOutputButton = document.querySelector("#newOutputButton");
const cwdOverlay = document.querySelector("#cwdOverlay");
const restoreOverlay = document.querySelector("#restoreOverlay");
const restoreOverlayText = document.querySelector("#restoreOverlayText");
const slashStrip = document.querySelector("#slashStrip");
const rootElement = document.documentElement;
const rootStyle = rootElement.style;
const bodyElement = document.body;

const slashCommands = ["/clear", "/ping", "/ctrlc", "/disconnect", "/kill", "/restart"];
const serverUrlStorageKey = "server-console.websocket-url.v2";
const legacyServerUrlStorageKey = "server-console.websocket-url";
const autoConnectStorageKey = "server-console.auto-connect";
const connectionCollapsedStorageKey = "server-console.connection-collapsed";
const transientTokenStorageKey = "server-console.websocket-token";
const reconnectBaseDelayMs = 1000;
const reconnectMaxDelayMs = 8000;
const closeBeforeReconnectDelayMs = 300;
const connectTimeoutMs = 10000;
const foregroundReconnectDelayMs = 250;
const outputFlushIntervalMs = 32;
const outputFlushMaxChars = 16 * 1024;
const outputFlushChunkChars = 8 * 1024;
const terminalReviewOutputPauseMs = 900;
const restoreFallbackCompleteMs = 700;
const appHeightChangeThresholdPx = 2;
const viewportLayoutSettleMs = 700;
const keyboardActivityWindowMs = 3000;
const backgroundSuspendDelayMs = 2500;
const terminalFitRetryDelayMs = 90;
const terminalFitMaxRetries = 6;
const viewportContent = "width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no, viewport-fit=cover";
const encoder = new TextEncoder();
let terminalDecoder = new TextDecoder();
const viewportMeta = document.querySelector("meta[name=\"viewport\"]");
const commandInputPlaceholder = commandInput.getAttribute("placeholder") || "";
let commandInputEmptyState = null;
let commandInputMinHeightValue = 64;
let lastSlashStripSignature = "";
let lastSentResizeCols = 0;
let lastSentResizeRows = 0;
let lastStatusKind = "";
let lastStatusLabel = "";
let lastStatusDetail = "";
let lastStatusConnected = null;
let lastStatusRestartPending = null;
let lastSessionLabel = "";
let lastPersistedServerUrl = "";
const nativeBootstrap = window.__serverConsoleNativeBootstrap || {};
const nativeBootstrapServerUrl = typeof nativeBootstrap.serverUrl === "string" ? nativeBootstrap.serverUrl.trim() : "";
const nativeBootstrapEnabled = nativeBootstrapServerUrl.length > 0;
const nativeWrapperMode = nativeBootstrap.hideChrome === true;

let socket = null;
let connected = false;
let fitAddon = null;
let term = null;
let resizeTimer = null;
let resizeAfterKeyboardTimer = 0;
let reconnectTimer = null;
let reconnectCountdownTimer = null;
let connectTimeoutTimer = null;
let foregroundReconnectTimer = null;
let reconnectEnabled = false;
let reconnectAttempts = 0;
let restartPending = false;
let outputQueue = [];
let outputQueueHead = 0;
let outputQueueChars = 0;
let outputFlushHandle = 0;
let outputFlushTimer = 0;
let inputFocused = false;
let lastInputBlurAt = 0;
let restoreActive = false;
let restoreExpectedChunks = 0;
let restoreReceivedChunks = 0;
let restoreQueuedChars = 0;
let restoreReplayBytes = 0;
let restoreShell = "";
let restoreFallbackTimer = 0;
let hasConnectedOnce = false;
let hasUnreadOutput = false;
let viewportResetTimer = 0;
let followLatestOutput = true;
let terminalScrollTrackingSuppressedUntil = 0;
let terminalWriteInProgress = 0;
let terminalReviewOutputPausedUntil = 0;
let terminalReviewScrollTop = null;
let preserveLatestOnScheduledFit = false;
let keyboardActivityUntil = 0;
let terminalInputFocused = false;
let backgroundSuspendTimer = 0;
let terminalTouchStartY = 0;
let terminalTouchActive = false;
let draftSubmitPending = false;
let userReviewingOutput = false;
let lastOutputSequence = 0;
let activeSessionCreatedAt = 0;
let transientConnectionToken = "";
let currentCwd = "";
let terminalViewportElement = null;
let terminalResizeObserver = null;
let terminalFitRetryTimer = 0;
let terminalFitRetryCount = 0;
let compactViewportMediaQuery = null;
let compactViewportMatches = false;
let compactViewportClassApplied = null;
let viewportLayoutFrame = 0;
let viewportLayoutDelayTimer = 0;
let newOutputButtonHidden = true;
let lastViewportHeight = 0;
let lastViewportWidth = 0;
let lastViewportGutter = 0;
let lastViewportTop = 0;
let lastViewportLeft = 0;
let lastConnectButtonText = "";
let lastConnectButtonAriaLabel = "";
let lastRestartButtonDisabled = null;
let commandInputHeight = 0;
let newOutputButtonUpdateFrame = 0;
let connectionCollapsedApplied = null;
let lastNewOutputButtonHidden = null;
let restoreOverlayVisible = null;
let lastRestoreOverlayText = "";

viewportMeta?.setAttribute("content", viewportContent);
forgetStoredCommandInputState();
if (nativeWrapperMode) {
  rootElement.setAttribute("data-native-wrapper", "1");
}
initViewportCache();
rememberPageToken();
const initialWebSocketUrl = nativeBootstrapServerUrl || savedWebSocketUrl();
serverUrl.value = stripTokenFromUrl(initialWebSocketUrl);
if (nativeBootstrapEnabled) {
  rememberAutoConnect(true);
}
persistServerUrl();
commandRow?.setAttribute("data-placeholder", commandInputPlaceholder);
commandInput.setAttribute("aria-label", commandInputPlaceholder);
setCommandInputValue("");
syncCompactViewportClass();
syncVisualViewport();
setConnectionCollapsed(savedConnectionCollapsed(), false);
updateSessionLabel();
autoGrow(commandInput);
updateSlashStrip();
initTerminal();

connectButton.addEventListener("click", () => {
  if (connected) {
    disconnect();
  } else {
    reconnectAttempts = 0;
    connect();
  }
});

restartButton.addEventListener("click", restartTerminal);
refreshButton.addEventListener("click", refreshApp);

connectionToggleButton.addEventListener("click", () => {
  setConnectionCollapsed(!connectionPanel.classList.contains("collapsed"));
});

serverUrl.addEventListener("change", persistServerUrl);
serverUrl.addEventListener("blur", persistServerUrl);

commandPanel?.querySelectorAll("button").forEach(button => {
  button.addEventListener("pointerdown", event => {
    event.preventDefault();
  });
});
escButton.addEventListener("click", event => { event.preventDefault(); sendToolByte(0x1b); });
slashButton.addEventListener("click", event => { event.preventDefault(); sendKeySequence("/"); });
upButton.addEventListener("click", event => { event.preventDefault(); sendKeySequence("\x1b[A"); });
downButton.addEventListener("click", event => { event.preventDefault(); sendKeySequence("\x1b[B"); });
enterButton.addEventListener("click", event => { event.preventDefault(); sendEnterAction(); });
newOutputButton.addEventListener("click", scrollTerminalToLatest);

commandInput.addEventListener("input", () => {
  noteKeyboardActivity();
  updateCommandInputEmptyState();
  autoGrow(commandInput);
  updateSlashStrip();
  resetIOSViewportScale();
});

commandInput.addEventListener("beforeinput", event => {
  noteKeyboardActivity();
  if (event.inputType === "insertLineBreak") {
    event.preventDefault();
    sendDraft();
  }
});

commandInput.addEventListener("keydown", event => {
  noteKeyboardActivity();
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    sendDraft();
    return;
  }

  resetIOSViewportScale();
});

commandInput.addEventListener("focus", () => {
  noteKeyboardActivity();
  inputFocused = true;
  if (!isUserReviewingOutput()) {
    followLatestOutput = true;
    hasUnreadOutput = false;
    suppressTerminalScrollTracking(viewportLayoutSettleMs);
  }
  updateCommandInputEmptyState();
  resetIOSViewportScale();
  updateViewportLayout();
});

commandInput.addEventListener("blur", () => {
  noteKeyboardActivity();
  inputFocused = false;
  lastInputBlurAt = Date.now();
  if (!isUserReviewingOutput()) {
    followLatestOutput = true;
    hasUnreadOutput = false;
    suppressTerminalScrollTracking(viewportLayoutSettleMs);
  }
  scheduleViewportLayoutAfterDelay(80);
});

window.addEventListener("resize", () => {
  noteViewportKeyboardActivity();
  scheduleViewportLayout();
});
window.addEventListener("orientationchange", () => {
  scheduleViewportLayout();
});
if (window.visualViewport) {
  window.visualViewport.addEventListener("resize", () => {
    noteViewportKeyboardActivity();
    scheduleViewportLayout();
  });
  window.visualViewport.addEventListener("scroll", () => {
    if (terminalTouchActive || isUserReviewingOutput()) return;
    noteViewportKeyboardActivity();
    scheduleViewportLayout();
  });
}
window.addEventListener("pagehide", () => {
  if (isKeyboardActivityRecent()) {
    updateViewportLayout();
    return;
  }
  scheduleBackgroundSuspend();
});
window.addEventListener("pageshow", () => {
  cancelBackgroundSuspend();
  scheduleViewportLayout();
  reconnectWhenVisible();
});
window.addEventListener("focus", () => {
  cancelBackgroundSuspend();
  scheduleViewportLayout();
  reconnectWhenVisible();
});
window.addEventListener("online", reconnectWhenVisible);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden") {
    if (isKeyboardActivityRecent()) return;
    scheduleBackgroundSuspend();
  } else {
    cancelBackgroundSuspend();
    scheduleViewportLayout();
    reconnectWhenVisible();
  }
});
document.addEventListener("freeze", () => {
  if (isKeyboardActivityRecent()) return;
  scheduleBackgroundSuspend();
});

function updateViewportLayout() {
  const preserveLatest = shouldPreserveLatestForLayout();
  if (preserveLatest) {
    followLatestOutput = true;
    hasUnreadOutput = false;
    suppressTerminalScrollTracking(viewportLayoutSettleMs);
  }
  const viewportChanged = syncVisualViewport();
  const compactChanged = syncCompactViewportClass();
  if (preserveLatest || viewportChanged || compactChanged) {
    scheduleFit(preserveLatest);
  }
}

function scheduleViewportLayout() {
  if (viewportLayoutFrame) return;
  viewportLayoutFrame = window.requestAnimationFrame(() => {
    viewportLayoutFrame = 0;
    updateViewportLayout();
  });
}

function scheduleViewportLayoutAfterDelay(delayMs = 80) {
  window.clearTimeout(viewportLayoutDelayTimer);
  viewportLayoutDelayTimer = window.setTimeout(() => {
    viewportLayoutDelayTimer = 0;
    scheduleViewportLayout();
  }, delayMs);
}

if (shouldAutoConnect() && !nativeWrapperMode) {
  window.setTimeout(connect, 0);
}

if (nativeBootstrapEnabled) {
  window.setTimeout(() => {
    if (!connected) connect();
  }, 0);
}

function defaultWebSocketUrl() {
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  const host = location.host || "127.0.0.1:8765";
  return `${protocol}//${host}/terminal?session=phone`;
}

function savedWebSocketUrl() {
  try {
    return withDefaultSession(
      localStorage.getItem(serverUrlStorageKey) ||
      localStorage.getItem(legacyServerUrlStorageKey) ||
      defaultWebSocketUrl()
    );
  } catch {
    return defaultWebSocketUrl();
  }
}

function persistServerUrl() {
  const rawValue = withDefaultSession(serverUrl.value.trim());
  rememberTransientToken(rawValue);
  const value = stripTokenFromUrl(rawValue);
  if (!value) return;
  if (serverUrl.value !== value) {
    serverUrl.value = value;
  }
  updateSessionLabel(value);

  try {
    if (lastPersistedServerUrl !== value) {
      localStorage.setItem(serverUrlStorageKey, value);
      localStorage.removeItem(legacyServerUrlStorageKey);
      lastPersistedServerUrl = value;
    }
  } catch {
    // Private browsing or storage restrictions should not block connecting.
  }
}

function stripTokenFromUrl(value) {
  if (!value) return value;

  try {
    const parsed = new URL(value, location.href);
    parsed.searchParams.delete("token");
    return parsed.toString();
  } catch {
    return value;
  }
}

function websocketUrl() {
  return stripTokenFromUrl(withDefaultSession(serverUrl.value.trim()));
}

function websocketProtocols() {
  const protocols = ["server-console"];
  const token = currentConnectionToken();
  if (token) {
    protocols.push(`server-console-token.${base64UrlEncodeUtf8(token)}`);
  }
  return protocols;
}

function base64UrlEncodeUtf8(value) {
  return bytesToBase64(encoder.encode(value))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/g, "");
}

function withDefaultSession(value) {
  if (!value) return value;

  try {
    const parsed = new URL(value, location.href);
    if (parsed.pathname === "/terminal" && !parsed.searchParams.has("session")) {
      parsed.searchParams.set("session", "phone");
      return parsed.toString();
    }
  } catch {
    return value;
  }

  return value;
}

function sessionNameFromUrl(value) {
  try {
    const parsed = new URL(value, location.href);
    return parsed.searchParams.get("session") || "phone";
  } catch {
    return "phone";
  }
}

function rememberTransientToken(value) {
  const token = tokenFromUrl(value);
  if (!token) return;

  rememberToken(token);
}

function rememberPageToken() {
  const token = tokenFromUrl(location.href);
  if (!token) return;

  rememberToken(token);

  try {
    const cleaned = new URL(location.href);
    cleaned.searchParams.delete("token");
    history.replaceState(null, "", `${cleaned.pathname}${cleaned.search}${cleaned.hash}`);
  } catch {
    // The token is still available for this tab even if URL cleanup fails.
  }
}

function rememberToken(token) {
  transientConnectionToken = token;
  try {
    sessionStorage.setItem(transientTokenStorageKey, token);
  } catch {
    // Token persistence is best-effort; the current URL still works for this connect attempt.
  }
}

function currentConnectionToken() {
  const directToken = tokenFromUrl(serverUrl.value);
  if (directToken) return directToken;
  if (transientConnectionToken) return transientConnectionToken;

  try {
    return sessionStorage.getItem(transientTokenStorageKey) || "";
  } catch {
    return "";
  }
}

function tokenFromUrl(value) {
  if (!value) return "";

  try {
    return new URL(value, location.href).searchParams.get("token") || "";
  } catch {
    return "";
  }
}

function updateSessionLabel(value = serverUrl.value) {
  const label = sessionNameFromUrl(value);
  if (lastSessionLabel === label) return;
  lastSessionLabel = label;
  sessionLabel.textContent = label;
}

function savedConnectionCollapsed() {
  try {
    const saved = localStorage.getItem(connectionCollapsedStorageKey);
    if (saved !== null) {
      return saved === "1";
    }
  } catch {
    return true;
  }

  return true;
}

function setConnectionCollapsed(collapsed, persist = true) {
  const changed = connectionCollapsedApplied !== collapsed;
  connectionCollapsedApplied = collapsed;
  if (changed) {
    connectionPanel.classList.toggle("collapsed", collapsed);
    connectionToggleButton.textContent = collapsed ? "›" : "⌄";
    connectionToggleButton.setAttribute("aria-expanded", collapsed ? "false" : "true");
    connectionToggleButton.setAttribute("aria-label", collapsed ? "展开连接设置" : "折叠连接设置");
  }
  if (persist) {
    try {
      localStorage.setItem(connectionCollapsedStorageKey, collapsed ? "1" : "0");
    } catch {
      // Layout preference is optional.
    }
  }
  if (changed) {
    scheduleFit(followLatestOutput);
  }
}

function rememberAutoConnect(enabled) {
  try {
    localStorage.setItem(autoConnectStorageKey, enabled ? "1" : "0");
  } catch {
    // The connection itself should continue even if storage is unavailable.
  }
}

function forgetStoredCommandInputState() {
  try {
    localStorage.removeItem("server-console.command-draft");
    localStorage.removeItem("server-console.command-history");
  } catch {
    // Clearing retired UI state is best-effort only.
  }
}

function clearDraft() {
  setCommandInputValue("");
}

function shouldAutoConnect() {
  try {
    return localStorage.getItem(autoConnectStorageKey) === "1";
  } catch {
    return false;
  }
}

function initViewportCache() {
  compactViewportMediaQuery = window.matchMedia("(max-width: 720px), (pointer: coarse)");
  refreshCompactViewportMatches();
  compactViewportClassApplied = null;
  terminalViewportElement = terminalElement.querySelector(".xterm-viewport");
  newOutputButtonHidden = newOutputButton.hidden;
  const onCompactViewportChange = event => {
    compactViewportMatches = !!event.matches;
    syncCompactViewportClass();
  };
  if (typeof compactViewportMediaQuery.addEventListener === "function") {
    compactViewportMediaQuery.addEventListener("change", onCompactViewportChange);
  } else if (typeof compactViewportMediaQuery.addListener === "function") {
    compactViewportMediaQuery.addListener(onCompactViewportChange);
  }
}

function refreshCompactViewportMatches() {
  if (!compactViewportMediaQuery) {
    compactViewportMediaQuery = window.matchMedia("(max-width: 720px), (pointer: coarse)");
  }
  compactViewportMatches = compactViewportMediaQuery.matches;
  return compactViewportMatches;
}

function initTerminal() {
  term = new Terminal({
    cursorBlink: true,
    convertEol: false,
    scrollback: terminalScrollback(),
    allowProposedApi: false,
    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace',
    fontSize: 13,
    lineHeight: 1.2,
    theme: {
      background: "#090c10",
      foreground: "#e8eef4",
      cursor: "#e8eef4",
      selectionBackground: "#33404d"
    }
  });
  fitAddon = new FitAddon.FitAddon();
  term.loadAddon(fitAddon);
  term.open(terminalElement);
  terminalViewportElement = terminalElement.querySelector(".xterm-viewport");
  observeTerminalSize();
  fitTerminal(true);
  attachTerminalKeyboardFocusTracking();

  const viewport = terminalViewport();
  if (viewport) {
    viewport.addEventListener("scroll", handleTerminalScroll);
    viewport.addEventListener("touchstart", handleTerminalTouchStart, { passive: true });
    viewport.addEventListener("touchmove", handleTerminalTouchMove, { passive: false });
    viewport.addEventListener("touchend", handleTerminalTouchEnd, { passive: true });
    viewport.addEventListener("touchcancel", handleTerminalTouchEnd, { passive: true });
    viewport.addEventListener("wheel", handleTerminalWheel, { passive: true });
  }

  term.onData(data => {
    sendBytes(encoder.encode(data));
  });
}

function connect() {
  if (document.visibilityState === "hidden") {
    if (reconnectEnabled) {
      setStatus("disconnected", "Paused", "Will reconnect when opened");
    }
    return;
  }

  if (socket) {
    if (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING) {
      return;
    }
    if (socket.readyState === WebSocket.CLOSING) {
      scheduleReconnect(closeBeforeReconnectDelayMs);
      return;
    }
  }

  reconnectEnabled = true;
  rememberAutoConnect(true);
  window.clearTimeout(reconnectTimer);
  window.clearInterval(reconnectCountdownTimer);
  window.clearTimeout(connectTimeoutTimer);
  resetRestoreState();
  updateCwdOverlay("");
  const existingSocket = socket;
  socket = null;
  if (existingSocket) {
    existingSocket.close();
  }
  setStatus("connecting", "Connecting", "Opening WebSocket");
  persistServerUrl();
  const url = websocketUrl();

  let currentSocket;
  try {
    currentSocket = new WebSocket(url, websocketProtocols());
  } catch (error) {
    restartPending = false;
    reconnectEnabled = false;
    rememberAutoConnect(false);
    setStatus("failed", "Connection failed", error.message);
    return;
  }

  socket = currentSocket;
  terminalDecoder = new TextDecoder();
  lastSentResizeCols = 0;
  lastSentResizeRows = 0;
  window.clearTimeout(connectTimeoutTimer);
  connectTimeoutTimer = window.setTimeout(() => {
    if (socket !== currentSocket || currentSocket.readyState !== WebSocket.CONNECTING) return;
    setStatus("failed", "Connect timeout", "Retrying after close");
    currentSocket.close();
  }, connectTimeoutMs);

  currentSocket.addEventListener("open", () => {
    if (socket !== currentSocket) return;
    window.clearTimeout(connectTimeoutTimer);
    reconnectAttempts = 0;
    window.clearInterval(reconnectCountdownTimer);
    connected = true;
    setStatus("connecting", "Waiting for shell", "WebSocket connected");
    if (!hasConnectedOnce) {
      term.clear();
      hasConnectedOnce = true;
    }
    if (isCompactViewport()) {
      setConnectionCollapsed(true);
    }
    fitTerminal(followLatestOutput);
    sendResize();
    focusTerminalIfIdle();
  });

  currentSocket.addEventListener("message", event => {
    if (socket !== currentSocket) return;
    handleServerMessage(event.data);
  });

  currentSocket.addEventListener("close", () => {
    if (socket !== currentSocket) return;
    window.clearTimeout(connectTimeoutTimer);
    flushTerminalDecoder();
    socket = null;
    connected = false;
    const restarting = restartPending;
    setStatus(restarting ? "connecting" : "disconnected", restarting ? "Restarting" : "Disconnected", restarting ? "Starting a new terminal" : (reconnectEnabled ? "Connection closed" : ""));
    scheduleReconnect(restarting ? closeBeforeReconnectDelayMs : undefined);
  });

  currentSocket.addEventListener("error", () => {
    if (socket !== currentSocket) return;
    setStatus("failed", "Connection error", "Waiting for close");
  });
}

function disconnect(updateStatus = true, disableReconnect = true, clearCurrentSocket = true) {
  restartPending = false;
  resetRestoreState();
  if (disableReconnect) {
    reconnectEnabled = false;
    rememberAutoConnect(false);
  }
  window.clearTimeout(reconnectTimer);
  window.clearInterval(reconnectCountdownTimer);
  window.clearTimeout(connectTimeoutTimer);
  window.clearTimeout(foregroundReconnectTimer);
  window.clearTimeout(resizeAfterKeyboardTimer);
  const currentSocket = socket;
  if (clearCurrentSocket) {
    socket = null;
  }
  if (currentSocket) {
    currentSocket.close();
  }
  connected = false;
  if (updateStatus) {
    setStatus("disconnected", "Disconnected", "");
  }
}

function scheduleReconnect(delayMs = null) {
  if (!reconnectEnabled) return;
  window.clearTimeout(reconnectTimer);
  window.clearInterval(reconnectCountdownTimer);
  if (document.visibilityState === "hidden") {
    setStatus("disconnected", "Paused", "Will reconnect when opened");
    return;
  }
  const delay = delayMs ?? Math.min(reconnectBaseDelayMs * 2 ** reconnectAttempts, reconnectMaxDelayMs);
  reconnectAttempts += 1;
  showReconnectCountdown(delay);
  reconnectTimer = window.setTimeout(() => {
    window.clearInterval(reconnectCountdownTimer);
    if (document.visibilityState === "hidden") return;
    if (!connected) connect();
  }, delay);
}

function reconnectWhenVisible() {
  if (!reconnectEnabled || connected || document.visibilityState === "hidden") return;
  if (socket && socket.readyState !== WebSocket.CLOSED) return;
  window.clearTimeout(foregroundReconnectTimer);
  foregroundReconnectTimer = window.setTimeout(() => {
    foregroundReconnectTimer = null;
    if (!reconnectEnabled || connected || document.visibilityState === "hidden") return;
    if (socket && socket.readyState !== WebSocket.CLOSED) return;
    connect();
  }, foregroundReconnectDelayMs);
}

function scheduleBackgroundSuspend() {
  if (backgroundSuspendTimer) return;
  backgroundSuspendTimer = window.setTimeout(() => {
    backgroundSuspendTimer = 0;
    if (isKeyboardActivityRecent()) return;
    if (document.visibilityState !== "hidden") return;
    suspendConnectionForBackground();
  }, backgroundSuspendDelayMs);
}

function cancelBackgroundSuspend() {
  window.clearTimeout(backgroundSuspendTimer);
  backgroundSuspendTimer = 0;
}

function suspendConnectionForBackground() {
  if (isKeyboardActivityRecent()) {
    updateViewportLayout();
    return;
  }
  if (!reconnectEnabled && !socket) return;
  window.clearTimeout(reconnectTimer);
  window.clearInterval(reconnectCountdownTimer);
  window.clearTimeout(connectTimeoutTimer);
  window.clearTimeout(foregroundReconnectTimer);
  window.clearTimeout(resizeAfterKeyboardTimer);
  const currentSocket = socket;
  resetRestoreState();
  socket = null;
  connected = false;
  if (currentSocket && currentSocket.readyState !== WebSocket.CLOSED) {
    currentSocket.close();
  }
  if (reconnectEnabled) {
    setStatus("disconnected", "Paused", "Will reconnect when opened");
  }
}

function noteKeyboardActivity() {
  keyboardActivityUntil = Date.now() + keyboardActivityWindowMs;
}

function noteViewportKeyboardActivity() {
  if (inputFocused || Date.now() - lastInputBlurAt <= keyboardActivityWindowMs) {
    noteKeyboardActivity();
  }
}

function isKeyboardActivityRecent() {
  return inputFocused || terminalInputFocused || Date.now() < keyboardActivityUntil;
}

function showReconnectCountdown(delayMs) {
  const startedAt = Date.now();
  const update = () => {
    const remainingMs = Math.max(0, delayMs - (Date.now() - startedAt));
    const seconds = Math.max(1, Math.ceil(remainingMs / 1000));
    setStatus("connecting", "Reconnecting", `Retry in ${seconds}s`);
  };
  update();
  reconnectCountdownTimer = window.setInterval(update, 1000);
}

function sendDraft() {
  if (draftSubmitPending) return;
  const value = commandInputDraftValue();
  const trimmed = value.trim();
  if (!trimmed) return;

  if (isLocalSlashCommand(trimmed)) {
    handleSlashCommand(trimmed);
    finishDraftSend();
    return;
  }

  draftSubmitPending = true;
  if (!sendBytes(encoder.encode(`${value}\r`))) {
    draftSubmitPending = false;
    stabilizeCommandInputAfterSend();
    return;
  }

  followLatestOutput = true;
  hasUnreadOutput = false;
  flushTerminalOutput(true, true);
  scrollTerminalToBottom();
  draftSubmitPending = false;
  finishDraftSend();
}

function finishDraftSend() {
  clearDraft();
  autoGrow(commandInput);
  updateSlashStrip();
  stabilizeCommandInputAfterSend();
}

function sendEnterAction() {
  if (commandInputDraftValue().trim()) {
    sendDraft();
    return;
  }

  sendKeySequence("\r");
}

function handleSlashCommand(command) {
  switch (command) {
    case "/clear":
      term.clear();
      break;
    case "/ping":
      sendJson({ type: "ping" });
      break;
    case "/ctrlc":
      sendBytes(new Uint8Array([0x03]));
      break;
    case "/disconnect":
      disconnect();
      break;
    case "/kill":
      sendJson({ type: "kill" });
      disconnect();
      break;
    case "/restart":
      restartTerminal();
      break;
    default:
      term.write(`\r\n[unknown local command] ${command}\r\n`);
  }
}

function restartTerminal() {
  if (restartPending || !connected || !socket || socket.readyState !== WebSocket.OPEN) return;
  restartPending = true;
  reconnectEnabled = true;
  rememberAutoConnect(true);
  reconnectAttempts = 0;
  window.clearTimeout(reconnectTimer);
  window.clearInterval(reconnectCountdownTimer);
  window.clearTimeout(foregroundReconnectTimer);
  resetRestoreState();
  updateCwdOverlay("");
  discardPendingTerminalOutput();
  term?.clear();
  setStatus("connecting", "Restarting", "Starting a new terminal");
  sendJson({ type: "restart" });
}

function discardPendingTerminalOutput() {
  outputQueue = [];
  outputQueueHead = 0;
  outputQueueChars = 0;
  hasUnreadOutput = false;
  if (outputFlushTimer) {
    window.clearTimeout(outputFlushTimer);
    outputFlushTimer = 0;
  }
  if (outputFlushHandle) {
    window.cancelAnimationFrame(outputFlushHandle);
    outputFlushHandle = 0;
  }
}

function sendBytes(bytes) {
  return sendRawJson(`{"type":"input","data":"${bytesToBase64(bytes)}"}`);
}

function sendToolByte(byte) {
  sendBytes(new Uint8Array([byte]));
  stabilizeViewportAfterTerminalAction();
}

function sendKeySequence(sequence) {
  sendBytes(encoder.encode(sequence));
  stabilizeViewportAfterTerminalAction();
}

function sendJson(message) {
  if (!ensureSocketReady()) return false;
  socket.send(JSON.stringify(message));
  return true;
}

function sendRawJson(payload) {
  if (!ensureSocketReady()) return false;
  socket.send(payload);
  return true;
}

function ensureSocketReady() {
  if (!socket || socket.readyState !== WebSocket.OPEN) {
    if (term) term.write("\r\n[not connected]\r\n");
    setStatus("connecting", "Not connected", "Will retry");
    scheduleReconnect();
    return false;
  }
  return true;
}

function handleServerMessage(raw) {
  if (handleFastOutputMessage(raw)) return;

  let message;
  try {
    message = JSON.parse(raw);
  } catch {
    term.write("\r\n[decode error]\r\n");
    return;
  }

  switch (message.type) {
    case "hello":
      restartPending = false;
      startRestoreState(message);
      break;
    case "cwd":
      updateCwdOverlay(message.cwd || "");
      break;
    case "output":
      handleOutputPayload(message.data || "", message.seq);
      break;
    case "error":
      term.write(`\r\n[server error] ${message.message || "unknown"}\r\n`);
      setStatus("failed", "Server error", message.message || "unknown");
      break;
    case "exit":
      flushTerminalDecoder();
      term.write(`\r\n[process exited: ${message.code ?? 0}]\r\n`);
      disconnect();
      break;
    case "pong":
      term.write(`\r\n[pong ${new Date().toLocaleTimeString()}]\r\n`);
      setStatus(connected ? "connected" : "connecting", connected ? "Connected" : "Connecting", "Pong received");
      break;
  }
}

function handleFastOutputMessage(raw) {
  if (typeof raw !== "string") return false;

  const prefix = "{\"type\":\"output\",\"data\":\"";
  if (!raw.startsWith(prefix)) return false;

  const dataEnd = raw.indexOf("\"", prefix.length);
  if (dataEnd < 0) return false;

  const rest = raw.slice(dataEnd + 1);
  if (rest === "}") {
    handleOutputPayload(raw.slice(prefix.length, dataEnd), 0);
    return true;
  }

  const sequencePrefix = ",\"seq\":";
  if (!rest.startsWith(sequencePrefix) || !rest.endsWith("}")) return false;
  const sequence = Number(rest.slice(sequencePrefix.length, -1));
  if (!Number.isSafeInteger(sequence) || sequence <= 0) return false;
  handleOutputPayload(raw.slice(prefix.length, dataEnd), sequence);
  return true;
}

function handleOutputPayload(data, sequenceValue) {
  const sequence = normalizedOutputSequence(sequenceValue);
  const restoreChunk = takeRestoreOutputChunk();
  if (!restoreChunk && shouldSkipOutputSequence(sequence)) {
    decodeTerminalOutput(data || "");
    noteSkippedRestoreChunk(restoreChunk);
    return;
  }
  enqueueTerminalOutput(decodeTerminalOutput(data || ""), restoreChunk, sequence);
}

function normalizedOutputSequence(value) {
  const sequence = Number(value);
  return Number.isSafeInteger(sequence) && sequence > 0 ? sequence : 0;
}

function shouldSkipOutputSequence(sequence) {
  return sequence > 0 && sequence <= lastOutputSequence;
}

function noteSkippedRestoreChunk(restoreChunk) {
  if (!restoreChunk) return;
  restoreReceivedChunks += 1;
  updateRestoreOverlay();
  finishRestoreIfReady();
}

function updateCwdOverlay(cwd) {
  const value = typeof cwd === "string" ? cwd.trim() : "";
  if (value === currentCwd) return;
  currentCwd = value;
  const compact = value ? compactPath(value) : "";
  if (cwdOverlay) {
    if (cwdOverlay.textContent === compact && cwdOverlay.title === value && cwdOverlay.hidden === (value.length === 0)) {
      return;
    }
    cwdOverlay.textContent = compact;
    cwdOverlay.title = value;
    cwdOverlay.hidden = value.length === 0;
  }
}

function compactPath(path) {
  const home = path === "~" || path.startsWith("~/") ? path : path.replace(/^\/home\/([^/]+)(?=\/|$)/, "~");
  const parts = home.split("/").filter(Boolean);
  if (home === "/" || parts.length <= 3) return home;
  const prefix = home.startsWith("/") ? "/" : "";
  return `${prefix}.../${parts.slice(-3).join("/")}`;
}

function updateSlashStrip() {
  const value = commandInputDraftValue().trim();
  if (!value.startsWith("/")) {
    if (lastSlashStripSignature) {
      lastSlashStripSignature = "";
      slashStrip.replaceChildren();
    }
    slashStrip.hidden = true;
    return;
  }

  const matches = slashCommands.filter(command => value === "/" || command.startsWith(value));
  const signature = `${value}\u0000${matches.join("\u0001")}`;
  if (signature === lastSlashStripSignature) {
    if (matches.length > 0) {
      slashStrip.hidden = false;
    }
    return;
  }
  lastSlashStripSignature = signature;
  slashStrip.replaceChildren();
  const fragment = document.createDocumentFragment();
  for (const command of matches) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = command;
    button.addEventListener("click", () => {
      setCommandInputValue(command);
      sendDraft();
    });
    fragment.append(button);
  }
  slashStrip.append(fragment);
  slashStrip.hidden = matches.length === 0;
}

function commandInputDraftValue() {
  return commandInput.value;
}

function setCommandInputValue(value) {
  commandInput.value = value || "";
  updateCommandInputEmptyState();
}

function updateCommandInputEmptyState() {
  const empty = commandInputDraftValue().length === 0;
  if (commandInputEmptyState === empty) return;
  commandInputEmptyState = empty;
  commandInput.classList.toggle("is-empty", empty);
  commandRow?.classList.toggle("is-empty", empty);
  commandInput.placeholder = empty ? "" : commandInputPlaceholder;
}

function isLocalSlashCommand(command) {
  return slashCommands.includes(command);
}

function terminalScrollback() {
  return isCompactViewport() ? 5000 : 10000;
}

function isCompactViewport() {
  return compactViewportMatches;
}

function enqueueTerminalOutput(text, restoreChunk = false, sequence = 0) {
  if (!text) {
    if (sequence > 0) {
      lastOutputSequence = Math.max(lastOutputSequence, sequence);
    }
    if (restoreChunk) {
      restoreReceivedChunks += 1;
      updateRestoreOverlay();
      finishRestoreIfReady();
    }
    return;
  }

  const wasFollowingLatest = shouldAutoFollowLatest() || (!isUserReviewingOutput() && isTerminalAtBottom());
  if (sequence > 0) {
    lastOutputSequence = Math.max(lastOutputSequence, sequence);
  }
  outputQueue.push({ text, restoreChunk });
  outputQueueChars += text.length;
  if (restoreChunk) {
    restoreReceivedChunks += 1;
    restoreQueuedChars += text.length;
    updateRestoreOverlay();
  }
  if (!wasFollowingLatest) {
    hasUnreadOutput = true;
  }
  if (outputQueueChars >= outputFlushMaxChars) {
    scheduleOutputFlush(0, true);
    return;
  }

  scheduleOutputFlush(outputFlushIntervalMs);
}

function scheduleOutputFlush(delayMs, force = false) {
  if ((outputFlushTimer || outputFlushHandle) && !force) return;
  if (outputFlushHandle) {
    window.cancelAnimationFrame(outputFlushHandle);
    outputFlushHandle = 0;
  }
  if (outputFlushTimer) {
    window.clearTimeout(outputFlushTimer);
    outputFlushTimer = 0;
  }
  if (!force && isTerminalReviewOutputPaused()) {
    delayMs = Math.max(delayMs, terminalReviewOutputPausedUntil - Date.now());
  }
  outputFlushTimer = window.setTimeout(() => {
    outputFlushTimer = 0;
    outputFlushHandle = window.requestAnimationFrame(() => flushTerminalOutput());
  }, delayMs);
}

function flushTerminalOutput(flushAll = false, forceStayAtBottom = false) {
  if (outputFlushTimer) {
    window.clearTimeout(outputFlushTimer);
    outputFlushTimer = 0;
  }
  if (outputFlushHandle) {
    window.cancelAnimationFrame(outputFlushHandle);
    outputFlushHandle = 0;
  }
  if (!forceStayAtBottom && !flushAll && isTerminalReviewOutputPaused()) {
    scheduleOutputFlush(terminalReviewOutputPausedUntil - Date.now());
    return;
  }
  if (!outputQueueChars || !term) {
    finishRestoreIfReady();
    return;
  }

  const reviewingOutput = isUserReviewingOutput() || terminalTouchActive || !isTerminalAtBottom();
  const preservedReviewScrollTop = reviewingOutput ? captureTerminalReviewScrollTop() : null;
  const text = takeQueuedTerminalOutput(flushAll === true ? outputQueueChars : outputFlushChunkChars);
  terminalWriteInProgress += 1;
  term.write(text, () => {
    terminalWriteInProgress = Math.max(0, terminalWriteInProgress - 1);
    if (reviewingOutput) {
      restoreTerminalReviewScrollTop(preservedReviewScrollTop);
      hasUnreadOutput = true;
    } else if (shouldStayAtBottom(forceStayAtBottom)) {
      scrollTerminalToBottom();
      followLatestOutput = true;
      hasUnreadOutput = false;
    } else if (!isTerminalAtBottom()) {
      hasUnreadOutput = true;
    }
    finishRestoreIfReady();
    if (outputQueueChars > 0) {
      scheduleOutputFlush(0, true);
    }
    updateNewOutputButton();
  });
}

function takeQueuedTerminalOutput(limitChars) {
  let remaining = limitChars;
  const parts = [];

  while (outputQueueHead < outputQueue.length && remaining > 0) {
    const item = outputQueue[outputQueueHead];
    if (item.text.length <= remaining) {
      const length = item.text.length;
      outputQueueHead += 1;
      parts.push(item.text);
      outputQueueChars -= length;
      remaining -= length;
      if (item.restoreChunk) {
        restoreQueuedChars -= length;
      }
      continue;
    }

    const chunk = item.text.slice(0, remaining);
    item.text = item.text.slice(remaining);
    parts.push(chunk);
    outputQueueChars -= chunk.length;
    if (item.restoreChunk) {
      restoreQueuedChars -= chunk.length;
    }
    remaining = 0;
  }

  compactOutputQueueIfNeeded();
  return parts.join("");
}

function compactOutputQueueIfNeeded() {
  if (outputQueueHead === 0) return;
  if (outputQueueHead === outputQueue.length) {
    outputQueue = [];
    outputQueueHead = 0;
    return;
  }
  if (outputQueueHead > 64 && outputQueueHead * 2 > outputQueue.length) {
    outputQueue = outputQueue.slice(outputQueueHead);
    outputQueueHead = 0;
  }
}

function startRestoreState(message) {
  const shell = message.shell || "shell";
  const createdAt = Number(message.createdAt) || 0;
  const hasReplayMetadata = Object.prototype.hasOwnProperty.call(message, "replayChunks");
  const replayChunks = Math.max(0, Number(message.replayChunks) || 0);
  const replayBytes = Math.max(0, Number(message.replayBytes) || 0);
  restartPending = false;
  restoreShell = shell;
  restoreReplayBytes = replayBytes;
  if (createdAt && createdAt !== activeSessionCreatedAt) {
    activeSessionCreatedAt = createdAt;
    lastOutputSequence = 0;
  }
  if (Object.prototype.hasOwnProperty.call(message, "cwd") && (message.cwd || !currentCwd)) {
    updateCwdOverlay(message.cwd || "");
  }

  if (message.persistent && (!hasReplayMetadata || replayChunks > 0)) {
    restoreActive = true;
    restoreExpectedChunks = hasReplayMetadata ? replayChunks : 1;
    restoreReceivedChunks = 0;
    restoreQueuedChars = 0;
    showRestoreOverlay("Restoring session");
    setStatus("connecting", "Restoring session", restoreDetailText());
    if (!hasReplayMetadata) {
      restoreFallbackTimer = window.setTimeout(() => {
        restoreReceivedChunks = restoreExpectedChunks;
        finishRestoreIfReady();
      }, restoreFallbackCompleteMs);
    }
    return;
  }

  resetRestoreState();
  setStatus("connected", "Connected", message.persistent ? `Session restored · ${shell}` : `${shell} ready`);
}

function takeRestoreOutputChunk() {
  return restoreActive && restoreReceivedChunks < restoreExpectedChunks;
}

function finishRestoreIfReady() {
  if (!restoreActive) return;
  if (restoreReceivedChunks < restoreExpectedChunks || restoreQueuedChars > 0) {
    updateRestoreOverlay();
    return;
  }

  const shell = restoreShell || "shell";
  resetRestoreState();
  setStatus("connected", "Connected", `Session restored · ${shell}`);
}

function resetRestoreState() {
  restoreActive = false;
  restoreExpectedChunks = 0;
  restoreReceivedChunks = 0;
  restoreQueuedChars = 0;
  restoreReplayBytes = 0;
  restoreShell = "";
  window.clearTimeout(restoreFallbackTimer);
  restoreFallbackTimer = 0;
  hideRestoreOverlay();
}

function decodeTerminalOutput(value) {
  return terminalDecoder.decode(base64ToBytes(value), { stream: true });
}

function flushTerminalDecoder() {
  const tail = terminalDecoder.decode();
  if (tail) {
    enqueueTerminalOutput(tail);
    flushTerminalOutput(true, true);
  }
  terminalDecoder = new TextDecoder();
}

function updateRestoreOverlay() {
  if (!restoreActive) return;
  const detail = restoreDetailText();
  showRestoreOverlay(`Restoring session ${detail}`);
  setStatus("connecting", "Restoring session", detail);
}

function restoreDetailText(prefix = "") {
  const received = Math.min(restoreReceivedChunks, restoreExpectedChunks);
  const total = restoreExpectedChunks || received;
  const byteText = restoreReplayBytes ? ` · ${formatBytes(restoreReplayBytes)}` : "";
  const progress = total ? `${received}/${total}` : "starting";
  return `${prefix ? `${prefix} ` : ""}${progress}${byteText}`;
}

function showRestoreOverlay(text) {
  if (!restoreOverlay) return;
  if (restoreOverlayVisible !== true) {
    restoreOverlayVisible = true;
    restoreOverlay.hidden = false;
  }
  if (restoreOverlayText) {
    if (lastRestoreOverlayText !== text) {
      lastRestoreOverlayText = text;
      restoreOverlayText.textContent = text;
    }
  }
}

function hideRestoreOverlay() {
  if (restoreOverlay) {
    if (restoreOverlayVisible !== false) {
      restoreOverlayVisible = false;
      restoreOverlay.hidden = true;
    }
  }
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function scrollTerminalPages(direction) {
  flushTerminalOutput(true);
  if (!term) return;
  term.scrollPages(direction);
  commandInput.focus({ preventScroll: true });
}

function scrollTerminalToLatest() {
  followLatestOutput = true;
  hasUnreadOutput = false;
  userReviewingOutput = false;
  terminalReviewOutputPausedUntil = 0;
  terminalReviewScrollTop = null;
  suppressTerminalScrollTracking(viewportLayoutSettleMs);
  flushTerminalOutput(true, true);
  if (!term) return;
  scrollTerminalToBottom();
  updateViewportLayout();
}

function keepCommandInputFocused() {
  try {
    commandInput.focus({ preventScroll: true });
  } catch {
    commandInput.focus();
  }
}

function stabilizeCommandInputAfterSend() {
  keepCommandInputFocused();
  resetIOSViewportScale();
  if (!isCompactViewport()) return;

  for (const delayMs of [80, 220]) {
    window.setTimeout(() => {
      keepCommandInputFocused();
      resetIOSViewportScale();
      updateViewportLayout();
    }, delayMs);
  }
}

function stabilizeViewportAfterTerminalAction() {
  resetIOSViewportScale();
  if (inputFocused || Date.now() - lastInputBlurAt <= 500) {
    stabilizeCommandInputAfterSend();
    return;
  }
  updateViewportLayout();
}

function terminalViewport() {
  if (terminalViewportElement && terminalViewportElement.isConnected) {
    return terminalViewportElement;
  }
  terminalViewportElement = terminalElement.querySelector(".xterm-viewport");
  return terminalViewportElement;
}

function isTerminalAtBottom() {
  const viewport = terminalViewport();
  if (!viewport) return true;
  return viewport.scrollTop + viewport.clientHeight >= viewport.scrollHeight - 8;
}

function handleTerminalScroll() {
  if (isTerminalScrollTrackingSuppressed()) {
    updateNewOutputButton();
    return;
  }

  if (isTerminalAtBottom()) {
    followLatestOutput = true;
    hasUnreadOutput = false;
    userReviewingOutput = false;
    terminalReviewOutputPausedUntil = 0;
    terminalReviewScrollTop = null;
  } else {
    markUserReviewingOutput();
  }

  updateNewOutputButton();
}

function handleTerminalTouchStart(event) {
  terminalTouchActive = true;
  terminalTouchStartY = event.touches?.[0]?.clientY || 0;
  terminalReviewScrollTop = terminalViewport()?.scrollTop ?? null;
  if (!isTerminalAtBottom()) {
    markUserReviewingOutput();
  }
}

function handleTerminalTouchMove(event) {
  const viewport = terminalViewport();
  const currentY = event.touches?.[0]?.clientY || terminalTouchStartY;
  const deltaY = currentY - terminalTouchStartY;
  if (viewport && Math.abs(deltaY) > 2) {
    markUserReviewingOutput();
    terminalReviewScrollTop = viewport.scrollTop;
  }
  if (!viewport || terminalWouldOverscroll(viewport, deltaY)) {
    event.preventDefault();
  }
}

function handleTerminalTouchEnd() {
  terminalTouchActive = false;
  if (isTerminalAtBottom()) {
    followLatestOutput = true;
    hasUnreadOutput = false;
    userReviewingOutput = false;
    terminalReviewOutputPausedUntil = 0;
    terminalReviewScrollTop = null;
  } else {
    markUserReviewingOutput();
  }
  updateNewOutputButton();
}

function handleTerminalWheel(event) {
  if (event.deltaY < 0 || !isTerminalAtBottom()) {
    markUserReviewingOutput();
  }
}

function terminalWouldOverscroll(viewport, deltaY) {
  if (viewport.scrollHeight <= viewport.clientHeight) return true;
  const atTop = viewport.scrollTop <= 0;
  const atBottom = viewport.scrollTop + viewport.clientHeight >= viewport.scrollHeight - 1;
  return (atTop && deltaY > 0) || (atBottom && deltaY < 0);
}

function updateNewOutputButton() {
  if (newOutputButtonUpdateFrame) return;
  newOutputButtonUpdateFrame = window.requestAnimationFrame(() => {
    newOutputButtonUpdateFrame = 0;
    const hidden = !hasUnreadOutput;
    if (lastNewOutputButtonHidden === hidden) return;
    lastNewOutputButtonHidden = hidden;
    setNewOutputButtonHidden(hidden);
  });
}

function setNewOutputButtonHidden(hidden) {
  if (newOutputButtonHidden === hidden) return;
  newOutputButtonHidden = hidden;
  newOutputButton.hidden = hidden;
}

function markUserReviewingOutput() {
  followLatestOutput = false;
  hasUnreadOutput = true;
  userReviewingOutput = true;
  terminalReviewOutputPausedUntil = Math.max(
    terminalReviewOutputPausedUntil,
    Date.now() + terminalReviewOutputPauseMs
  );
  updateNewOutputButton();
}

function isUserReviewingOutput() {
  return userReviewingOutput;
}

function shouldAutoFollowLatest() {
  return followLatestOutput && !isUserReviewingOutput() && !terminalTouchActive;
}

function shouldStayAtBottom(forceStayAtBottom = false) {
  return (forceStayAtBottom || followLatestOutput) && !isUserReviewingOutput() && !terminalTouchActive;
}

function isTerminalReviewOutputPaused() {
  return isUserReviewingOutput() && Date.now() < terminalReviewOutputPausedUntil;
}

function captureTerminalReviewScrollTop() {
  const viewport = terminalViewport();
  if (!viewport) return null;
  terminalReviewScrollTop = viewport.scrollTop;
  return terminalReviewScrollTop;
}

function restoreTerminalReviewScrollTop(scrollTop) {
  const viewport = terminalViewport();
  if (!viewport || scrollTop === null) return;
  viewport.scrollTop = Math.max(0, Math.min(scrollTop, viewport.scrollHeight - viewport.clientHeight));
  terminalReviewScrollTop = viewport.scrollTop;
}

function detachTerminalFromBottom(viewport = terminalViewport()) {
  if (!viewport) return;
  const maxScrollTop = Math.max(0, viewport.scrollHeight - viewport.clientHeight);
  if (viewport.scrollTop + viewport.clientHeight >= viewport.scrollHeight - 1) {
    viewport.scrollTop = Math.max(0, maxScrollTop - 48);
  }
  terminalReviewScrollTop = viewport.scrollTop;
}

function focusTerminalIfIdle() {
  if (!term || inputFocused || isCompactViewport()) return;
  term.focus();
}

function attachTerminalKeyboardFocusTracking() {
  const helperTextarea = term?.textarea || terminalElement.querySelector(".xterm-helper-textarea");
  if (!helperTextarea) return;

  helperTextarea.addEventListener("focus", () => {
    terminalInputFocused = true;
    noteKeyboardActivity();
    if (!isUserReviewingOutput()) {
      followLatestOutput = true;
      hasUnreadOutput = false;
      suppressTerminalScrollTracking(viewportLayoutSettleMs);
    }
    updateViewportLayout();
  });

  helperTextarea.addEventListener("blur", () => {
    terminalInputFocused = false;
    noteKeyboardActivity();
    if (!isUserReviewingOutput()) {
      followLatestOutput = true;
      hasUnreadOutput = false;
      suppressTerminalScrollTracking(viewportLayoutSettleMs);
    }
    scheduleViewportLayoutAfterDelay(80);
  });
}

function restoreInputFocusIfRecent() {
  if (inputFocused || Date.now() - lastInputBlurAt > 500) return false;
  try {
    commandInput.focus({ preventScroll: true });
  } catch {
    commandInput.focus();
  }
  return true;
}

function syncVisualViewport() {
  const viewport = window.visualViewport;
  const height = viewport?.height || window.innerHeight;
  const width = viewport?.width || window.innerWidth;
  const useViewportOffset = isKeyboardActivityRecent() && !terminalTouchActive && !isUserReviewingOutput();
  const top = useViewportOffset ? (viewport?.offsetTop || 0) : 0;
  const left = useViewportOffset ? (viewport?.offsetLeft || 0) : 0;
  let changed = false;
  if (Number.isFinite(height) && height > 0) {
    const roundedHeight = Math.round(height);
    if (roundedHeight !== lastViewportHeight) {
      lastViewportHeight = roundedHeight;
      rootStyle.setProperty("--app-height", `${roundedHeight}px`);
      changed = true;
    }
  }
  if (Number.isFinite(width) && width > 0) {
    const roundedWidth = Math.round(width);
    const gutter = Math.max(0, Math.round((roundedWidth - 980) / 2));
    if (roundedWidth !== lastViewportWidth) {
      lastViewportWidth = roundedWidth;
      rootStyle.setProperty("--app-width", `${roundedWidth}px`);
      changed = true;
    }
    if (gutter !== lastViewportGutter) {
      lastViewportGutter = gutter;
      rootStyle.setProperty("--app-gutter", `${gutter}px`);
      changed = true;
    }
  }
  if (Number.isFinite(top)) {
    const roundedTop = Math.round(top);
    if (roundedTop !== lastViewportTop) {
      lastViewportTop = roundedTop;
      rootStyle.setProperty("--app-top", `${roundedTop}px`);
      changed = true;
    }
  }
  if (Number.isFinite(left)) {
    const roundedLeft = Math.round(left);
    if (roundedLeft !== lastViewportLeft) {
      lastViewportLeft = roundedLeft;
      rootStyle.setProperty("--app-left", `${roundedLeft}px`);
      changed = true;
    }
  }
  return changed;
}

function syncCompactViewportClass() {
  const next = refreshCompactViewportMatches();
  if (compactViewportClassApplied === next) return false;
  compactViewportClassApplied = next;
  bodyElement.classList.toggle("compact-ui", next);
  refreshCommandInputMinHeight();
  return true;
}

function scrollTerminalToBottom() {
  if (!term) return;
  suppressTerminalScrollTracking(160);
  term.scrollToBottom();
  followLatestOutput = true;
  hasUnreadOutput = false;
  userReviewingOutput = false;
  terminalReviewOutputPausedUntil = 0;
  terminalReviewScrollTop = null;
  updateNewOutputButton();
}

function scrollTerminalToBottomSoon() {
  requestAnimationFrame(() => {
    if (shouldAutoFollowLatest()) {
      scrollTerminalToBottom();
    }
  });
}

function suppressTerminalScrollTracking(durationMs) {
  terminalScrollTrackingSuppressedUntil = Math.max(
    terminalScrollTrackingSuppressedUntil,
    Date.now() + durationMs
  );
}

function isTerminalScrollTrackingSuppressed() {
  return Date.now() < terminalScrollTrackingSuppressedUntil;
}

function shouldPreserveLatestForLayout() {
  if (isUserReviewingOutput() || terminalTouchActive) return false;
  return inputFocused || Date.now() - lastInputBlurAt <= viewportLayoutSettleMs || followLatestOutput;
}

function resetIOSViewportScale() {
  if (!isIOSLike() || !viewportMeta) return;

  viewportMeta.setAttribute("content", `${viewportContent}, minimum-scale=1`);
  window.clearTimeout(viewportResetTimer);
  viewportResetTimer = window.setTimeout(() => {
    viewportMeta.setAttribute("content", viewportContent);
    viewportResetTimer = 0;
  }, 120);
}

function isIOSLike() {
  return /iP(ad|hone|od)/.test(navigator.platform) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
}

function autoGrow(element) {
  const minHeight = commandInputMinHeight();
  const previousHeight = commandInputHeight || element.offsetHeight;
  element.style.height = `${minHeight}px`;
  const nextHeight = Math.max(minHeight, Math.min(element.scrollHeight, 120));
  element.style.height = `${nextHeight}px`;
  commandInputHeight = nextHeight;
  if (Math.abs(nextHeight - previousHeight) >= appHeightChangeThresholdPx) {
    scheduleFit(shouldPreserveLatestForLayout());
  }
}

function commandInputMinHeight() {
  return commandInputMinHeightValue;
}

function refreshCommandInputMinHeight() {
  const value = window.getComputedStyle(rootElement).getPropertyValue("--command-input-min-height");
  const parsed = Number.parseFloat(value);
  if (Number.isFinite(parsed) && parsed > 0) {
    commandInputMinHeightValue = parsed;
  }
}

function observeTerminalSize() {
  if (terminalResizeObserver || typeof ResizeObserver !== "function") return;

  terminalResizeObserver = new ResizeObserver(entries => {
    const entry = entries[0];
    const rect = entry?.contentRect;
    if (!rect || rect.width <= 0 || rect.height <= 0) return;
    scheduleFit(shouldPreserveLatestForLayout());
  });
  terminalResizeObserver.observe(terminalElement);
}

function fitTerminal(forceStayAtBottom = false) {
  if (!fitAddon || !term) return;
  const proposed = fitAddon.proposeDimensions?.();
  if (!proposed || !Number.isFinite(proposed.cols) || !Number.isFinite(proposed.rows)) {
    scheduleTerminalFitRetry(forceStayAtBottom);
    return;
  }
  const stayAtBottom = shouldStayAtBottom(forceStayAtBottom);
  if (stayAtBottom) {
    suppressTerminalScrollTracking(viewportLayoutSettleMs);
  }
  fitAddon.fit();
  terminalFitRetryCount = 0;
  window.clearTimeout(terminalFitRetryTimer);
  terminalFitRetryTimer = 0;
  refreshTerminal();
  if (stayAtBottom) {
    scrollTerminalToBottom();
    window.setTimeout(scrollTerminalToBottomSoon, 80);
    window.setTimeout(scrollTerminalToBottomSoon, 260);
  }
}

function scheduleTerminalFitRetry(forceStayAtBottom) {
  if (terminalFitRetryCount >= terminalFitMaxRetries || terminalFitRetryTimer) return;
  terminalFitRetryCount += 1;
  terminalFitRetryTimer = window.setTimeout(() => {
    terminalFitRetryTimer = 0;
    fitTerminal(forceStayAtBottom);
    sendResize();
  }, terminalFitRetryDelayMs);
}

function refreshTerminal() {
  if (!term) return;
  try {
    term.refresh(0, Math.max(0, term.rows - 1));
  } catch {
    // Older xterm builds may render immediately after fit without refresh support.
  }
}

function scheduleFit(forceStayAtBottom = false) {
  preserveLatestOnScheduledFit = preserveLatestOnScheduledFit || forceStayAtBottom;
  window.clearTimeout(resizeTimer);
  resizeTimer = window.setTimeout(() => {
    const shouldPreserveLatest = preserveLatestOnScheduledFit;
    preserveLatestOnScheduledFit = false;
    fitTerminal(shouldPreserveLatest);
    sendResize();
  }, 120);
}

function sendResize() {
  if (!term || !socket || socket.readyState !== WebSocket.OPEN) return;
  if (isKeyboardActivityRecent()) {
    scheduleResizeAfterKeyboard();
    return;
  }
  const cols = term.cols;
  const rows = term.rows;
  if (cols === lastSentResizeCols && rows === lastSentResizeRows) return;
  lastSentResizeCols = cols;
  lastSentResizeRows = rows;
  socket.send(`{"type":"resize","cols":${cols},"rows":${rows}}`);
}

function scheduleResizeAfterKeyboard() {
  window.clearTimeout(resizeAfterKeyboardTimer);
  resizeAfterKeyboardTimer = window.setTimeout(() => {
    resizeAfterKeyboardTimer = 0;
    sendResize();
  }, Math.max(120, keyboardActivityUntil - Date.now() + 80));
}

function setStatus(kind, label, detail = "") {
  const nextConnected = connected;
  const nextRestartPending = restartPending;
  if (
    lastStatusKind === kind &&
    lastStatusLabel === label &&
    lastStatusDetail === detail &&
    lastStatusConnected === nextConnected &&
    lastStatusRestartPending === nextRestartPending
  ) {
    return;
  }
  lastStatusKind = kind;
  lastStatusLabel = label;
  lastStatusDetail = detail;
  lastStatusConnected = nextConnected;
  lastStatusRestartPending = nextRestartPending;
  const nextStatusClassName = `status-dot ${kind}`;
  if (statusDot.className !== nextStatusClassName) {
    statusDot.className = nextStatusClassName;
  }
  if (statusDot.dataset.kind !== kind) {
    statusDot.dataset.kind = kind;
  }
  statusText.textContent = label;
  statusDetail.textContent = detail;
  const nextConnectButtonText = connected ? "Disconnect" : "Connect";
  if (lastConnectButtonText !== nextConnectButtonText) {
    lastConnectButtonText = nextConnectButtonText;
    connectButton.textContent = nextConnectButtonText;
  }
  const nextConnectButtonAriaLabel = connected ? "断开连接" : "连接服务器";
  if (lastConnectButtonAriaLabel !== nextConnectButtonAriaLabel) {
    lastConnectButtonAriaLabel = nextConnectButtonAriaLabel;
    connectButton.setAttribute("aria-label", nextConnectButtonAriaLabel);
  }
  const nextRestartButtonDisabled = !connected || restartPending;
  if (lastRestartButtonDisabled !== nextRestartButtonDisabled) {
    lastRestartButtonDisabled = nextRestartButtonDisabled;
    restartButton.disabled = nextRestartButtonDisabled;
  }
}

function bytesToBase64(bytes) {
  const chunkSize = 0x8000;
  let binary = "";
  for (let index = 0; index < bytes.length; index += chunkSize) {
    const chunk = bytes.subarray(index, index + chunkSize);
    for (let offset = 0; offset < chunk.length; offset += 1) {
      binary += String.fromCharCode(chunk[offset]);
    }
  }
  return btoa(binary);
}

function base64ToBytes(value) {
  const binary = atob(value);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes;
}

function refreshApp() {
  refreshButton.disabled = true;
  refreshButton.textContent = "Refreshing";
  setStatus(connected ? "connected" : "connecting", connected ? "Connected" : "Refreshing", "Updating app");

  const reload = () => window.location.reload();
  if (!("serviceWorker" in navigator)) {
    reload();
    return;
  }

  navigator.serviceWorker.ready
    .then(registration => registration.update())
    .catch(() => {})
    .finally(reload);
}

function checkForAppUpdate() {
  if (!("serviceWorker" in navigator)) return;
  navigator.serviceWorker.ready
    .then(registration => registration.update())
    .catch(() => {});
}

function registerServiceWorker() {
  navigator.serviceWorker.register("/service-worker.js", { updateViaCache: "none" })
    .then(registration => {
      registration.update().catch(() => {});
      registration.addEventListener("updatefound", () => {
        const worker = registration.installing;
        if (!worker) return;
        worker.addEventListener("statechange", () => {
          if (worker.state === "installed" && navigator.serviceWorker.controller) {
            setStatus("connecting", "Updating", "Reloading app");
          }
        });
      });
    })
    .catch(() => {});
}

if ("serviceWorker" in navigator) {
  registerServiceWorker();
}
