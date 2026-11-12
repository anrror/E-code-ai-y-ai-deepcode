import { lazy, Suspense, useMemo, useState } from "react";
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
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const tree = useMemo(() => buildTree(workbench.entries), [workbench.entries]);

  const toggle = (path: string) => {
    setCollapsed((current) => {
      const next = new Set(current);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  const renderNode = (node: TreeNode, depth: number): React.ReactNode => {
    const isDirectory = node.kind === "directory";
    const open = !collapsed.has(node.path);
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
              {open ? "▾" : "▸"}
            </button>
          ) : (
            <span className={styles.treeSpacer} />
          )}
          <button
            type="button"
            className={styles.treeLabel}
            title={node.path}
            onClick={() =>
              isDirectory ? toggle(node.path) : void onOpenFile(node.path)
            }
          >
            <span aria-hidden="true">
              {isDirectory ? (open ? "📂" : "📁") : "·"}
            </span>
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
      <div
        className={styles.fileTree}
        aria-label={t("inspector.files.label", "Workspace files")}
      >
        {tree.map((node) => renderNode(node, 0))}
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
