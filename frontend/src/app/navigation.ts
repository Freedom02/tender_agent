import type { Component } from "vue";
import { ClipboardCheck, Database, FileText, Search, Settings, Waypoints } from "lucide-vue-next";

export type AppTab =
  | "catalog"
  | "follow-up"
  | "matching"
  | "workspace"
  | "generator"
  | "libraries"
  | "library-documents"
  | "config";

export interface NavigationItem {
  key: AppTab;
  label: string;
  icon: Component;
  count: number;
}

export interface StatItem {
  label: string;
  value: number | string;
  icon: Component;
}

export function createNavigationItems(counts: Record<AppTab, number>): NavigationItem[] {
  return [
    { key: "catalog", label: "招标项目", icon: Search, count: counts.catalog },
    { key: "follow-up", label: "跟进项目", icon: ClipboardCheck, count: counts["follow-up"] },
    { key: "matching", label: "匹配评估", icon: Waypoints, count: counts.matching },
    { key: "workspace", label: "标书工作台", icon: FileText, count: counts.workspace },
    { key: "libraries", label: "企业资料库", icon: Database, count: counts.libraries },
    { key: "config", label: "自动化与配置", icon: Settings, count: counts.config }
  ];
}
