import { lazy, Suspense, useMemo, useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  ChevronsDownUp,
  ChevronsUpDown,
  File,
  FileCode2,
  FileJson,
  FileText,
  Folder,
  FolderOpen,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { useSystemDarkMode } from "../../app/useSystemDarkMode";
import type { FileEntry } from "../../generated/app-server";
import type { CodeWorkbenchController } from "../workbench/useCodeWorkbench";
import { InspectorEmpty } from "./InspectorEmpty";
import { languageFor } from "./inspectorFormat";
import styles from "./Inspector.module.css";

const LocalMonacoEditor = lazy(() => import("../workbench/LocalMonacoEditor"));

interface TreeNode {
  name: string;
  path: string;
  kind: FileEntry["kind"];
  children: TreeNode[];
}

function fileIcon(name: string) {
  const lower = name.toLowerCase();
  if (lower.endsWith(".json") || lower.endsWith(".jsonc")) return FileJson;
  if (
    /\.(ts|tsx|js|jsx|mjs|cjs|py|rs|go|java|kt|cs|c|cpp|h|rb|php|swift|sh|ps1)$/.test(
      lower,
    )
  )
    return FileCode2;
  if (/\.(md|mdx|txt|rst|yaml|yml|toml|ini)$/.test(lower)) return FileText;
  return File;
}

function buildTree(entries: FileEntry[]): TreeNode[] {
  const root: TreeNode = { name: "", path: "", kind: "directory", children: [] };
  const byPath = new Map<string, TreeNode>([["", root]]);
  const ensure = (path: string): TreeNode => {
    const existing = byPath.get(path);
    if (existing) return existing;
    const parts = path.split("/");
    const parent = ensure(parts.slice(0, -1).join("/"));
    const node: TreeNode = {
      name: parts[parts.length - 1],
      path,
      kind: "directory",
      children: [],
    };
    parent.children.push(node);
    byPath.set(path, node);
    return node;
  };
  for (const entry of entries) {
    if (!entry.path) continue;
    const segments = entry.path.split("/");
    if (entry.hidden || segments.some((segment) => segment.startsWith("."))) {
      continue;
    }
    const parentPath = entry.path.includes("/")
      ? entry.path.slice(0, entry.path.lastIndexOf("/"))
      : "";
    const parent = ensure(parentPath);
    if (entry.kind === "directory") {
      ensure(entry.path);
    } else {
      parent.children.push({
        name: entry.name,
        path: entry.path,
        kind: entry.kind,
        children: [],
      });
    }
  }
  const sort = (nodes: TreeNode[]) => {
    nodes.sort(
      (left, right) =>
        (left.kind === "directory" ? 0 : 1) -
          (right.kind === "directory" ? 0 : 1) ||
        left.name.localeCompare(right.name),
    );
    nodes.forEach((node) => sort(node.children));
  };
  sort(root.children);
  return root.children;
}

interface FilesPanelProps {
  trusted: boolean;
  hasActiveTurn: boolean;
  workbench: CodeWorkbenchController;
  onOpenFile(path: string): Promise<void>;
  onDownload?: (path: string) => Promise<void>;
}

export function FilesPanel({
  trusted,
  hasActiveTurn,
  workbench,
  onOpenFile,
  onDownload,
}: FilesPanelProps) {
  const { t } = useTranslation();
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const darkMode = useSystemDarkMode();
  const dirty = Boolean(
    workbench.file && workbench.draft !== workbench.file.content,
  );
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const tree = useMemo(() => buildTree(workbench.entries), [workbench.entries]);

  const toggle = (path: string) => {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  const renderNode = (node: TreeNode, depth: number): React.ReactNode => {
    const isDirectory = node.kind === "directory";
    const open = expanded.has(node.path);
    return (
      <div key={node.path}>
        <div
          className={styles.treeRow}
          style={{ paddingLeft: `${8 + depth * 12}px` }}
        >
          {isDirectory ? (
            <button
              type="button"
              className={styles.treeChevron}
              aria-label={open ? "折叠" : "展开"}
              onClick={() => toggle(node.path)}
            >
              {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
            </button>
          ) : (
            <span className={styles.treeSpacer} />
          )}
          <span className={styles.treeIcon} aria-hidden="true">
            {isDirectory ? (
              open ? (
                <FolderOpen size={14} />
              ) : (
                <Folder size={14} />
              )
            ) : (
              (() => {
                const Icon = fileIcon(node.name);
                return <Icon size={14} />;
              })()
            )}
          </span>
          <button
            type="button"
            className={styles.treeLabel}
            title={node.path}
            onClick={() =>
              isDirectory ? toggle(node.path) : void onOpenFile(node.path)
            }
          >
            {node.name}
          </button>
        </div>
        {isDirectory && open
          ? node.children.map((child) => renderNode(child, depth + 1))
          : null}
      </div>
    );
  };

  return (
    <div className={styles.filesView}>
      <div className={styles.fileTreeWrap}>
        <div className={styles.treeHeader}>
          <span className={styles.treeTitle}>
            {t("inspector.files.label", "资源管理器")}
          </span>
          <span className={styles.treeCount}>{workbench.entries.length}</span>
          <span className={styles.treeActions}>
            <button
              type="button"
              title="全部展开"
              aria-label="全部展开"
              onClick={() =>
                setExpanded(
                  new Set(
                    workbench.entries
                      .filter((entry) => entry.kind === "directory")
                      .map((entry) => entry.path),
                  ),
                )
              }
            >
              <ChevronsUpDown size={13} />
            </button>
            <button
              type="button"
              title="全部折叠"
              aria-label="全部折叠"
              onClick={() => setExpanded(new Set())}
            >
              <ChevronsDownUp size={13} />
            </button>
          </span>
        </div>
        <div
          className={styles.fileTree}
          aria-label={t("inspector.files.label", "Workspace files")}
        >
          {tree.map((node) => renderNode(node, 0))}
        </div>
        {workbench.entriesTruncated ? (
          <p className={styles.treeNotice}>
            {t(
              "inspector.files.truncated",
              "File list limited to the first 750 entries.",
            )}
          </p>
        ) : null}
      </div>
      {workbench.file ? (
        <div className={styles.fileEditor}>
          <header>
            <span title={workbench.file.path}>{workbench.file.path}</span>
            <div>
              {onDownload && <button type="button" onClick={() => {
                setDownloadError(null);
                void onDownload(workbench.file!.path).catch((error) => setDownloadError(String(error)));
              }}>{t("inspector.files.download", "Download")}</button>}
              {workbench.file.truncated ? (
                <small>
                  {t("inspector.files.truncatedReadOnly", "Truncated · read-only")}
                </small>
              ) : dirty ? (
                <small>{t("inspector.files.unsaved", "Unsaved")}</small>
              ) : null}
              <button
                type="button"
                onClick={() => void workbench.saveFile()}
                disabled={
                  !trusted ||
                  hasActiveTurn ||
                  workbench.file.truncated ||
                  !dirty ||
                  workbench.loading
                }
              >
                {t("inspector.files.save", "Save")}
              </button>
            </div>
          </header>
          {downloadError && <p role="alert">{downloadError}</p>}
          <Suspense
            fallback={
              <div className={styles.editorLoading}>
                {t("inspector.files.loadingEditor", "Loading editor…")}
              </div>
            }
          >
            <LocalMonacoEditor
              height="100%"
              language={languageFor(workbench.file.path)}
              value={workbench.draft}
              onChange={(value) => workbench.setDraft(value ?? "")}
              theme={darkMode ? "vs-dark" : "vs-light"}
              options={{
                minimap: { enabled: false },
                readOnly:
                  !trusted || hasActiveTurn || workbench.file.truncated,
                fontSize: 12,
                lineHeight: 20,
                renderLineHighlight: "line",
                scrollBeyondLastLine: false,
                wordWrap: "on",
                automaticLayout: true,
              }}
            />
          </Suspense>
          {hasActiveTurn ? (
            <p className={styles.editorNotice}>
              {t(
                "inspector.files.locked",
                "Editing is locked until the active Turn finishes.",
              )}
            </p>
          ) : null}
        </div>
      ) : (
        <InspectorEmpty
          label={t("inspector.files.empty", "Select a text file to inspect it.")}
        />
      )}
    </div>
  );
}
