import type { Project, SkillInfo, Thread } from "../../generated/app-server";
import type { DesktopDestination } from "../../app/useDesktopUi";
import type { ClientRuntime } from "../../rpc/contracts";
import { AutomationsPage } from "../automations/AutomationsPage";
import { SkillsPage } from "../extensions/SkillsPage";
import { PluginsPage } from "../plugins/PluginsPage";
import { McpPage } from "../mcp/McpPage";
import { TeamPage } from "../team/TeamPage";

interface ManagementWorkspaceProps {
  destination: DesktopDestination;
  runtime: ClientRuntime;
  project: Project | null;
  onProjectsChanged?: () => void;
  onThreadCreated(thread: Thread): void;
  onOpenThread(threadId: string): void;
  onCreateSkill(skill: SkillInfo): Promise<void>;
}

export function ManagementWorkspace({
  destination,
  runtime,
  project,
  onProjectsChanged,
  onThreadCreated,
  onOpenThread,
  onCreateSkill,
}: ManagementWorkspaceProps) {
  if (destination === "automations") {
    return (
      <AutomationsPage
        key={project?.id ?? "global"}
        runtime={runtime}
        project={project}
        onThreadCreated={onThreadCreated}
        onOpenThread={onOpenThread}
      />
    );
  }
  if (destination === "skills") {
    return (
      <SkillsPage
        runtime={runtime}
        project={project}
        onCreateSkill={onCreateSkill}
      />
    );
  }
  if (destination === "plugins") {
    return <PluginsPage runtime={runtime} />;
  }
  if (destination === "team") {
    return <TeamPage
      runtime={runtime}
      project={project}
      onProjectsChanged={onProjectsChanged}
    />;
  }
  return <McpPage runtime={runtime} project={project} />;
}
