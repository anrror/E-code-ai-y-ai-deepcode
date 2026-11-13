import { useEffect, useRef, useState } from "react";
import { ChevronDown, ChevronRight, Square } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { ClientRuntime } from "../../rpc/contracts";
import { InspectorEmpty } from "./InspectorEmpty";
import styles from "./Inspector.module.css";

interface ProcessSummary {
  id: string;
  command: string;
  workspace: string;
  pid: number | null;
  status: string;
  exitCode: number | null;
  startedAt: number;
  endedAt: number | null;
}

interface ProcessesPanelProps {
  runtime: ClientRuntime;
  workspace: string | null;
}

const STATUS_LABEL: Record<string, string> = {
  running: "运行中",
  exited: "已结束",
  "timed out": "超时",
  cancelled: "已取消",
};

export function ProcessesPanel({ runtime, workspace }: ProcessesPanelProps) {
  const { t } = useTranslation();
  const [processes, setProcesses] = useState<ProcessSummary[]>([]);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [logs, setLogs] = useState<Record<string, string>>({});
  const offsets = useRef<Record<string, number>>({});
  const logRef = useRef<HTMLPreElement | null>(null);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const result = await runtime.request(
          "process/list",
          workspace ? { workspace } : {},
        );
        if (alive && result) {
          setProcesses(result.processes);
        }
      } catch {
        // A transient list failure leaves the previous snapshot on screen.
      }
    };
    void tick();
    const timer = setInterval(() => void tick(), 2000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [runtime, workspace]);

  useEffect(() => {
    if (!expanded) {
      return;
    }
    let alive = true;
    const tick = async () => {
      try {
        const result = await runtime.request("process/read", {
          processId: expanded,
          offset: offsets.current[expanded] ?? 0,
          limit: 65536,
        });
        if (!alive || !result) {
          return;
        }
        offsets.current[expanded] = result.nextOffset;
        if (result.data) {
          setLogs((current) => ({
            ...current,
            [expanded]: (current[expanded] ?? "") + result.data,
          }));
        }
      } catch {
        // The process may have been pruned; the list refresh will drop it.
      }
    };
    void tick();
    const timer = setInterval(() => void tick(), 1200);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [expanded, runtime]);

  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [logs, expanded]);

  const stop = async (processId: string) => {
    try {
      await runtime.request("process/stop", { processId });
      setProcesses((current) =>
        current.map((item) =>
          item.id === processId ? { ...item, status: "cancelled" } : item,
        ),
      );
    } catch {
      // Leave the row unchanged; the next list poll shows the real state.
    }
  };

  const visible = workspace
    ? processes.filter((item) => item.workspace === workspace)
    : processes;

  if (visible.length === 0) {
    return (
      <InspectorEmpty
        label={t(
          "inspector.processes.empty",
          "这个会话还没有启动过命令。代理运行命令后，这里会显示它的输出。",
        )}
        compact
      />
    );
  }

  return (
    <div className={styles.content}>
      <p className={styles.muted}>
        {t(
          "inspector.processes.hint",
          "代理在会话中启动的命令。展开查看实时输出，运行中的命令可以结束。",
        )}
      </p>
      <div className={styles.processList}>
        {visible.map((process) => (
          <section key={process.id} className={styles.processRow}>
            <button
              type="button"
              className={styles.processHeader}
              onClick={() =>
                setExpanded(expanded === process.id ? null : process.id)
              }
            >
              {expanded === process.id ? (
                <ChevronDown size={14} />
              ) : (
                <ChevronRight size={14} />
              )}
              <span
                className={
                  process.status === "running"
                    ? styles.processDotRunning
                    : styles.processDot
                }
              />
              <code className={styles.processCommand}>{process.command}</code>
              <span className={styles.processMeta}>
                {STATUS_LABEL[process.status] ?? process.status}
                {process.pid ? ` · pid ${process.pid}` : ""}
                {process.exitCode !== null ? ` · exit ${process.exitCode}` : ""}
                {` · ${new Date(process.startedAt * 1000).toLocaleTimeString()}`}
              </span>
              {process.status === "running" ? (
                <button
                  type="button"
                  className={styles.processStop}
                  onClick={(event) => {
                    event.stopPropagation();
                    void stop(process.id);
                  }}
                  title={t("inspector.processes.stop", "结束进程")}
                >
                  <Square size={12} />
                  {t("inspector.processes.stop", "结束进程")}
                </button>
              ) : null}
            </button>
            {expanded === process.id ? (
              logs[process.id] ? (
                <pre ref={logRef} className={styles.processLog}>
                  {logs[process.id]}
                </pre>
              ) : (
                <p className={styles.processNote}>
                  {process.id.startsWith("os:")
                    ? t(
                        "inspector.processes.external",
                        "该进程由应用外部启动，标准输出无法直接捕获；已在项目目录中查找最新的 .log 文件。",
                      )
                    : t("inspector.processes.waiting", "等待输出…")}
                </p>
              )
            ) : null}
          </section>
        ))}
      </div>
    </div>
  );
}
