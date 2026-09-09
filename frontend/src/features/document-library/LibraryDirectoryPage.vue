<script setup lang="ts">
import { computed, ref } from "vue";
import {
  BadgeCheck,
  Box,
  FileCheck2,
  FolderOpen,
  History,
  Info,
  Search,
  ShieldCheck,
  UsersRound
} from "lucide-vue-next";
import type {
  CompanyKnowledge,
  KnowledgeObject,
  KnowledgeObjectDetail,
  LibrarySummary
} from "../../shared/types";

const props = defineProps<{
  company: Record<string, any>;
  companyKnowledge: CompanyKnowledge | null;
  knowledgeDetail: KnowledgeObjectDetail | null;
  knowledgeDetailLoading: boolean;
  libraries: LibrarySummary[];
  selectedLibraryKey: string;
}>();

const emit = defineEmits<{
  selectLibrary: [key: string];
  openObject: [objectType: string, objectId: string];
}>();

const activeObjectType = ref("products");
const objectKeyword = ref("");
const detailVisible = ref(false);

const objectCategories = [
  { key: "businessDirections", label: "业务方向", icon: Box },
  { key: "industryScenarios", label: "行业场景", icon: UsersRound },
  { key: "products", label: "产品与服务", icon: Box },
  { key: "capabilities", label: "技术能力", icon: BadgeCheck },
  { key: "qualifications", label: "资质与认证", icon: ShieldCheck },
  { key: "projectReferences", label: "项目业绩", icon: History },
  { key: "materials", label: "企业材料", icon: FolderOpen },
  { key: "evidences", label: "证据", icon: FileCheck2 },
  { key: "sourceFiles", label: "来源文件", icon: FolderOpen },
  { key: "intellectualProperties", label: "知识产权", icon: BadgeCheck },
  { key: "personnel", label: "人员与团队", icon: UsersRound },
  { key: "deliveryCapabilities", label: "交付能力", icon: Box }
];

const activeCategory = computed(
  () => objectCategories.find((item) => item.key === activeObjectType.value) || objectCategories[0]
);

const activeObjects = computed<KnowledgeObject[]>(() => {
  const values = props.companyKnowledge?.[activeObjectType.value as keyof CompanyKnowledge];
  const items = Array.isArray(values) ? (values as KnowledgeObject[]) : [];
  const keyword = objectKeyword.value.trim().toLowerCase();
  if (!keyword) return items;
  return items.filter((item) => JSON.stringify(item).toLowerCase().includes(keyword));
});

const pendingVerificationCount = computed(() => {
  let count = 0;
  objectCategories.forEach((category) => {
    const values = props.companyKnowledge?.[category.key as keyof CompanyKnowledge];
    if (!Array.isArray(values)) return;
    (values as KnowledgeObject[]).forEach((item) => {
      const status = `${item.status || ""} ${item.verificationStatus || ""}`;
      if (/(待核验|待补|待确认|待上传)/.test(status)) count += 1;
    });
  });
  return count;
});

const gapCount = computed(() => {
  const gaps = props.company.dataQuality?.gaps;
  return Array.isArray(gaps) ? gaps.length : 0;
});

function categoryCount(key: string) {
  const values = props.companyKnowledge?.[key as keyof CompanyKnowledge];
  return Array.isArray(values) ? values.length : 0;
}

function objectStatus(item: KnowledgeObject) {
  return item.verificationStatus || item.status || "已建模";
}

function evidenceLevel(item: KnowledgeObject) {
  return item.evidenceLevel || item.level || "待核验";
}

function statusTagType(status: string) {
  if (/(待核验|待补|待确认|待上传)/.test(status)) return "warning";
  if (/历史/.test(status)) return "info";
  return "success";
}

function openObject(item: KnowledgeObject, objectType = activeObjectType.value) {
  if (!item.id) return;
  detailVisible.value = true;
  emit("openObject", objectType, item.id);
}
</script>

<template>
  <div class="library-directory-page">
    <el-card class="panel" shadow="never">
      <template #header>
        <div class="panel-header">
          <div class="library-heading">
            <el-icon><FolderOpen /></el-icon>
            <span>企业资料库</span>
          </div>
          <el-tag type="info" effect="plain">{{ libraries.length }} 个资料分区</el-tag>
        </div>
      </template>

      <div class="company-model-summary">
        <div class="company-model-heading">
          <div>
            <span class="section-eyebrow">当前投标主体</span>
            <strong>{{ company.companyName || company.company || "未配置公司主体" }}</strong>
          </div>
          <el-tag type="success" effect="plain">
            {{ companyKnowledge?.meta?.version || "V0.1" }} 对象模型
          </el-tag>
        </div>
        <p class="panel-subtitle">
          {{ companyKnowledge?.meta?.note || "公司资料将以对象、关系和证据形式参与后续招标匹配。" }}
        </p>
        <div class="company-model-counts">
          <div class="company-model-count">
            <strong>{{ companyKnowledge?.counts?.businessDirections || 0 }}</strong>
            <span>业务方向</span>
          </div>
          <div class="company-model-count">
            <strong>{{ companyKnowledge?.counts?.products || 0 }}</strong>
            <span>产品</span>
          </div>
          <div class="company-model-count">
            <strong>{{ companyKnowledge?.counts?.capabilities || 0 }}</strong>
            <span>技术能力</span>
          </div>
          <div class="company-model-count">
            <strong>{{ companyKnowledge?.counts?.qualifications || 0 }}</strong>
            <span>资质</span>
          </div>
          <div class="company-model-count">
            <strong>{{ companyKnowledge?.counts?.projectReferences || 0 }}</strong>
            <span>项目业绩</span>
          </div>
          <div class="company-model-count">
            <strong>{{ pendingVerificationCount }}</strong>
            <span>待核验事项</span>
          </div>
          <div class="company-model-count">
            <strong>{{ gapCount }}</strong>
            <span>资料缺口</span>
          </div>
        </div>
      </div>

      <div v-if="libraries.length" class="library-directory-grid">
        <button
          v-for="library in libraries"
          :key="library.key"
          type="button"
          class="library-directory-card"
          :class="{ active: library.key === selectedLibraryKey }"
          @click="emit('selectLibrary', library.key)"
        >
          <span class="library-card-icon"><el-icon :size="22"><FolderOpen /></el-icon></span>
          <span class="library-card-content">
            <strong>{{ library.name }}</strong>
            <small>{{ library.description || "企业投标相关资料" }}</small>
          </span>
          <el-tag size="small" effect="plain">{{ library.documentCount }} 份</el-tag>
        </button>
      </div>
    </el-card>

    <el-card class="panel ontology-browser-panel" shadow="never">
      <template #header>
        <div class="panel-header">
          <div>
            <div class="library-heading">
              <el-icon><Info /></el-icon>
              <span>对象分类浏览</span>
            </div>
            <p class="panel-subtitle">选择对象可查看关联证据、来源文件和本体关系。</p>
          </div>
          <el-input
            v-model="objectKeyword"
            :prefix-icon="Search"
            clearable
            placeholder="搜索当前分类"
            class="document-search"
          />
        </div>
      </template>

      <div class="knowledge-category-list">
        <button
          v-for="category in objectCategories"
          :key="category.key"
          type="button"
          class="knowledge-category-button"
          :class="{ active: activeObjectType === category.key }"
          @click="activeObjectType = category.key"
        >
          <el-icon><component :is="category.icon" /></el-icon>
          <span>{{ category.label }}</span>
          <small>{{ categoryCount(category.key) }}</small>
        </button>
      </div>

      <div class="knowledge-list-heading">
        <strong>{{ activeCategory.label }}</strong>
        <span>{{ activeObjects.length }} 条</span>
      </div>

      <el-table v-if="activeObjects.length" :data="activeObjects" stripe class="knowledge-object-table">
        <el-table-column label="对象名称" min-width="300">
          <template #default="{ row }">
            <button class="knowledge-object-name" type="button" @click="openObject(row)">
              {{ row.name || row.id }}
            </button>
            <small v-if="row.description">{{ row.description }}</small>
          </template>
        </el-table-column>
        <el-table-column label="证据强度" width="120">
          <template #default="{ row }">
            <el-tag size="small" effect="plain" :type="evidenceLevel(row) === '强' ? 'success' : 'info'">
              {{ evidenceLevel(row) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="核验状态" min-width="190">
          <template #default="{ row }">
            <el-tag size="small" :type="statusTagType(objectStatus(row))">{{ objectStatus(row) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="详情" width="88" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openObject(row)">查看</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-else :description="`${activeCategory.label}暂无已建模对象，后续请补充对应资料。`" />
    </el-card>

    <el-drawer v-model="detailVisible" :title="knowledgeDetail?.object.name || '对象详情'" size="680px">
      <el-skeleton v-if="knowledgeDetailLoading" :rows="10" animated />
      <template v-else-if="knowledgeDetail">
        <el-descriptions :column="1" border class="knowledge-detail-descriptions">
          <el-descriptions-item label="对象类型">{{ knowledgeDetail.objectTypeLabel }}</el-descriptions-item>
          <el-descriptions-item label="对象编号">{{ knowledgeDetail.object.id }}</el-descriptions-item>
          <el-descriptions-item label="核验状态">
            <el-tag size="small" :type="statusTagType(objectStatus(knowledgeDetail.object))">
              {{ objectStatus(knowledgeDetail.object) }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="证据强度">
            <el-tag size="small" effect="plain" :type="evidenceLevel(knowledgeDetail.object) === '强' ? 'success' : 'info'">
              {{ evidenceLevel(knowledgeDetail.object) }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item v-if="knowledgeDetail.object.description" label="说明">
            {{ knowledgeDetail.object.description }}
          </el-descriptions-item>
          <el-descriptions-item v-if="knowledgeDetail.object.locator" label="定位">
            {{ knowledgeDetail.object.locator }}
          </el-descriptions-item>
        </el-descriptions>

        <section class="knowledge-detail-section">
          <h3>关联证据</h3>
          <el-empty v-if="!knowledgeDetail.evidences.length" description="当前对象暂无直接证据记录" :image-size="60" />
          <div v-else class="knowledge-detail-list">
            <button v-for="evidence in knowledgeDetail.evidences" :key="evidence.id" class="knowledge-detail-row" type="button" @click="openObject(evidence, 'evidences')">
              <span>
                <strong>{{ evidence.name || evidence.id }}</strong>
                <small>{{ evidence.locator || "定位待补充" }}</small>
              </span>
              <el-tag size="small" effect="plain" :type="evidenceLevel(evidence) === '强' ? 'success' : 'info'">
                {{ evidenceLevel(evidence) }}
              </el-tag>
            </button>
          </div>
        </section>

        <section class="knowledge-detail-section">
          <h3>来源文件</h3>
          <el-empty v-if="!knowledgeDetail.sourceFiles.length" description="当前对象暂无直接来源文件记录" :image-size="60" />
          <div v-else class="knowledge-detail-list">
            <button v-for="source in knowledgeDetail.sourceFiles" :key="source.id" class="knowledge-detail-row" type="button" @click="openObject(source, 'sourceFiles')">
              <span>
                <strong>{{ source.name || source.id }}</strong>
                <small>{{ source.sourcePath || source.purpose || "来源已登记，待上传原件" }}</small>
              </span>
              <el-tag size="small" :type="statusTagType(objectStatus(source))">{{ objectStatus(source) }}</el-tag>
            </button>
          </div>
        </section>

        <section class="knowledge-detail-section">
          <h3>本体关系</h3>
          <el-empty v-if="!knowledgeDetail.related.length" description="当前对象暂无显式关系边" :image-size="60" />
          <div v-else class="knowledge-detail-list">
            <button
              v-for="relation in knowledgeDetail.related"
              :key="relation.id"
              class="knowledge-detail-row"
              type="button"
              :disabled="!relation.relatedObject"
              @click="relation.relatedObject && openObject(relation.relatedObject, relation.relatedObject.objectType)"
            >
              <span>
                <strong>
                  {{ relation.direction === "outgoing" ? `${relation.relation} ->` : `<- ${relation.relation}` }}
                  {{ relation.relatedObject?.name || relation.relatedObject?.id || "公司主体" }}
                </strong>
                <small>{{ relation.relatedObject?.objectTypeLabel || relation.toType || relation.fromType }}</small>
              </span>
              <el-tag size="small" effect="plain">{{ relation.relatedObject?.objectTypeLabel || "关系" }}</el-tag>
            </button>
          </div>
        </section>
      </template>
      <el-empty v-else description="未能加载对象详情" />
    </el-drawer>
  </div>
</template>
