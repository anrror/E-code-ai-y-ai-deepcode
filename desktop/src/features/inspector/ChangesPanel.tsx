import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import type { Thread } from "../../generated/app-server";
import { confirmAction } from "../../platform/confirmAction";
import type { CodeWorkbenchController } from "../workbench/useCodeWorkbench";
import { InspectorEmpty } from "./InspectorEmpty";
import styles from "./Inspector.module.css";

interface CommitSummary {
  hash: string;
  short: string;
  subject: string;
  author: string;
  date: string;
}

function patchLineClass(line: string): string {
  if (line.startsWith("+++") || line.startsWith("---")) return styles.patchMeta;
  if (line.startsWith("+")) return styles.patchAdd;
  if (line.startsWith("-")) return styles.patchRemove;
  if (line.startsWith("@@")) return styles.patchHunk;
  if (line.startsWith("commit ") || line.startsWith("Author:") || line.startsWith("Date:"))
    return styles.patchMeta;
  return styles.patchLine;
}

interface ChangesPanelProps {
  thread: Thread | null;
  trusted: boolean;
  hasActiveTurn: boolean;
  workbench: CodeWorkbenchController;
  onOpenFile(path: string): void;
}

export function ChangesPanel({
  thread,
  trusted,
  hasActiveTurn,
  workbench,
  onOpenFile,
}: ChangesPanelProps) {
  const { t } = useTranslation();
  const [view, setView] = useState<"changes" | "history">("changes");
  const [commits, setCommits] = useState<CommitSummary[]>([]);
  const [commit, setCommit] = useState<CommitSummary | null>(null);
  const [patch, setPatch] = useState("");
  const [historyLoading, setHistoryLoading] = useState(false);

  useEffect(() => {
    if (view !== "history" || !thread) {
      return;
    }
    let cancelled = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- loading flag for the async fetch
    setHistoryLoading(true);
    void workbench.runtime
      .request("git/log", { threadId: thread.id, limit: 100 })
      .then((result) => {
        if (cancelled) {
          return;
        }
        setCommits(result.commits);
        setCommit(result.commits[0] ?? null);
      })
      .catch(() => {
        if (!cancelled) {
          setCommits([]);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setHistoryLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [view, thread, workbench.runtime]);

  useEffect(() => {
    if (!thread || !commit) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- clearing the diff is a render input
      setPatch("");
      return;
    }
    let cancelled = false;
    void workbench.runtime
      .request("git/show", { threadId: thread.id, revision: commit.hash })
      .then((result) => {
        if (!cancelled) {
          setPatch(result.text);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setPatch("");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [thread, commit, workbench.runtime]);

  const cleanWorktree = async () => {
    const dirty = Boolean(workbench.git?.entries.length);
    if (
      dirty &&
      !(await confirmAction(
        t(
          "inspector.changes.cleanConfirm",
          "This worktree has uncommitted changes. Permanently remove it and discard them?",
        ),
        {
          confirmLabel: t("inspector.changes.removeWorktree", "Remove worktree"),
        },
      ))
    ) {
      return;
    }
    await workbench.resolveWorktree("clean", dirty);
  };

  const discardFile = async (path: string, revision: string) => {
    const file = workbench.diffs.find(
      (candidate) => candidate.path === path && candidate.revision === revision,
    );
    if (
      !file ||
      !(await confirmAction(
        t(
          "inspector.changes.discardConfirm",
          "Discard every staged and unstaged change to {{path}}? This cannot be undone.",
          { path },
        ),
        {
          confirmLabel: t("inspector.changes.discardChanges", "Discard changes"),
        },
      ))
    ) {
      return;
    }
    await workbench.discardChange(file);
  };

  return (
    <div className={styles.content}>
      <div className={styles.heading}>
        <div>
          <p className={styles.eyebrow}>
            {t("inspector.changes.eyebrow", "Git workspace")}
          </p>
          <h2>
            {workbench.git?.branch ??
              t("inspector.changes.noBranch", "No Git branch")}
          </h2>
        </div>
        <button type="button" onClick={() => void workbench.refresh()}>
          {t("inspector.changes.refresh", "Refresh")}
        </button>
      </div>

      <div className={styles.segmented}>
        <button
          type="button"
          className={view === "changes" ? styles.segmentedActive : undefined}
          onClick={() => setView("changes")}
        >
          {t("inspector.changes.tabChanges", "变更")}
        </button>
        <button
          type="button"
          className={view === "history" ? styles.segmentedActive : undefined}
          onClick={() => setView("history")}
        >
          {t("inspector.changes.tabHistory", "提交历史")}
        </button>
      </div>

      {view === "history" ? (
        <div className={styles.diffList}>
          <div className={styles.historyList}>
            {historyLoading ? (
              <p className={styles.note}>{t("inspector.changes.loadingHistory", "读取提交记录…")}</p>
            ) : commits.length === 0 ? (
              <InspectorEmpty label={t("inspector.changes.emptyHistory", "还没有提交记录")} compact />
            ) : (
              commits.map((item) => (
                <button
                  key={item.hash}
                  type="button"
                  className={
                    item.hash === commit?.hash ? styles.historyRowActive : styles.historyRow
                  }
                  onClick={() => setCommit(item)}
                >
                  <code>{item.short}</code>
                  <span className={styles.historySubject}>{item.subject}</span>
                  <span className={styles.historyMeta}>
                    {item.author} · {item.date.slice(0, 16).replace("T", " ")}
                  </span>
                </button>
              ))
            )}
          </div>
          <pre className={styles.commitPatch}>
            {patch
              ? patch.split("\n").map((line, index) => (
                  <span key={index} className={patchLineClass(line)}>
                    {line || " "}
                  </span>
                ))
              : null}
          </pre>
        </div>
      ) : (
        <>
      {thread?.worktreePath ? (
        <div className={styles.worktreeControl}>
          <strong>{t("inspector.changes.isolated", "Isolated worktree")}</strong>
          <span>{thread.worktreePath}</span>
          <div>
            <button
              type="button"
              disabled={!trusted || hasActiveTurn || workbench.loading}
              onClick={() => void workbench.resolveWorktree("keep")}
            >
              {t("inspector.changes.keepWorktree", "Keep worktree")}
            </button>
            <button
              type="button"
              disabled={!trusted || hasActiveTurn || workbench.loading}
              onClick={() => void cleanWorktree()}
            >
              {t("inspector.changes.cleanWorktree", "Clean worktree")}
            </button>
          </div>
        </div>
      ) : (
        <button
          className={styles.outlinedAction}
          type="button"
          onClick={() => void workbench.createWorktree()}
          disabled={
            !trusted || hasActiveTurn || !workbench.git || workbench.loading
          }
        >
          {t("inspector.changes.isolate", "Isolate this Session in a worktree")}
        </button>
      )}

      {workbench.gitError ? (
        <InspectorEmpty label={workbench.gitError} compact />
      ) : workbench.diffs.length ? (
        <div className={styles.diffList}>
          {workbench.diffs.map((file) => (
            <section key={file.path} className={styles.fileDiff}>
              <header>
                <strong>{file.path}</strong>
                <span>
                  <b>+{file.additions}</b> <i>−{file.deletions}</i>
                </span>
              </header>
              <div className={styles.diffActions}>
                <button
                  type="button"
                  disabled={file.binary || file.status === "deleted"}
                  onClick={() => onOpenFile(file.path)}
                >
                  {t("inspector.changes.openInEditor", "Open in editor")}
                </button>
                <button
                  type="button"
                  disabled={!trusted || hasActiveTurn || workbench.loading}
                  onClick={() => void discardFile(file.path, file.revision)}
                >
                  {t("inspector.changes.discardFile", "Discard file")}
                </button>
              </div>
              {file.binary ? (
                <p className={styles.binaryNotice}>
                  {t("inspector.changes.binary", "Binary file changed")}
                </p>
              ) : (
                file.hunks.map((hunk, hunkIndex) => (
                  <div className={styles.diffHunk} key={`${file.path}-${hunkIndex}`}>
                    <div className={styles.hunkHeader}>
                      −{hunk.oldStart},{hunk.oldLines} +{hunk.newStart},{hunk.newLines}
                    </div>
                    {hunk.lines.map((line, lineIndex) => (
                      <div
                        className={styles.diffLine}
                        data-kind={line.kind}
                        key={`${line.kind}-${lineIndex}`}
                      >
                        <span>{line.oldLine ?? ""}</span>
                        <span>{line.newLine ?? ""}</span>
                        <code>
                          {line.kind === "addition"
                            ? "+"
                            : line.kind === "deletion"
                              ? "−"
                              : " "}
                          {line.text}
                        </code>
                      </div>
                    ))}
                  </div>
                ))
              )}
            </section>
          ))}
        </div>
      ) : (
        <InspectorEmpty
          label={t("inspector.changes.clean", "Working tree is clean.")}
          compact
        />
      )}
      {hasActiveTurn ? (
        <p className={styles.note}>
          {t(
            "inspector.changes.locked",
            "File changes and worktree operations are locked while a Turn is active.",
          )}
        </p>
      ) : null}
        </>
      )}
    </div>
  );
}
