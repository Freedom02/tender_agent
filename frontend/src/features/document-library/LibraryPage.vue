<script setup lang="ts">
import { computed, ref } from "vue";
import { ElMessage } from "element-plus";
import { Edit3, FilePlus2, FolderOpen, Search, Trash2, Upload } from "lucide-vue-next";
import type { LibrarySummary } from "../../shared/types";

const props = defineProps<{
  libraries: LibrarySummary[];
  selectedLibrary: LibrarySummary | null;
  libraryForm: {
    key: string;
    name: string;
    text: string;
  };
  busy: boolean;
}>();

const emit = defineEmits<{
  save: [];
  fileSelected: [file: File | null];
}>();

const dialogVisible = ref(false);
const searchText = ref("");
const selectedFileName = ref("");

const filteredDocuments = computed(() => {
  const keyword = searchText.value.trim().toLowerCase();
  const documents = props.selectedLibrary?.documents || [];
  if (!keyword) return documents;
  return documents.filter((document) => document.name.toLowerCase().includes(keyword));
});

function openAddDialog() {
  selectedFileName.value = "";
  dialogVisible.value = true;
}

function onFileChange(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0] || null;
  selectedFileName.value = file?.name || "";
  emit("fileSelected", file);
}

function saveDocument() {
  emit("save");
  dialogVisible.value = false;
}

function editDocument(name: string) {
  ElMessage.info(`“${name}”编辑功能将在后续版本接入`);
}

function deleteDocument(name: string) {
  ElMessage.info(`“${name}”删除功能将在后续版本接入`);
}
</script>

<template>
  <div class="library-page">
    <el-row :gutter="16">
      <el-col :xs="24" :lg="7" :xl="6">
        <el-card class="panel library-directory-panel" shadow="never">
          <template #header>
            <div class="panel-header">
              <div class="library-heading">
                <el-icon><FolderOpen /></el-icon>
                <span>资料库目录</span>
              </div>
              <el-tag type="info" effect="plain">{{ libraries.length }} 个</el-tag>
            </div>
          </template>

          <div class="library-directory">
            <button
              v-for="library in libraries"
              :key="library.key"
              type="button"
              class="library-directory-item"
              :class="{ active: library.key === libraryForm.key }"
              @click="libraryForm.key = library.key"
            >
              <span class="library-directory-main">
                <span class="library-directory-name">{{ library.name }}</span>
                <small>{{ library.description || "企业投标相关资料" }}</small>
              </span>
              <el-tag size="small" effect="plain">{{ library.documentCount }}</el-tag>
            </button>
          </div>
        </el-card>
      </el-col>

      <el-col :xs="24" :lg="17" :xl="18">
        <el-card class="panel document-list-panel" shadow="never">
          <template #header>
            <div class="panel-header">
              <div>
                <div class="library-heading">
                  <el-icon><FolderOpen /></el-icon>
                  <span>{{ selectedLibrary?.name || "资料列表" }}</span>
                </div>
                <p class="panel-subtitle">
                  {{ selectedLibrary?.description || "管理当前资料库中的企业材料。" }}
                </p>
              </div>
              <el-button :icon="FilePlus2" type="primary" @click="openAddDialog">添加资料</el-button>
            </div>
          </template>

          <div class="document-list-toolbar">
            <div class="document-count">
              共 {{ selectedLibrary?.documentCount || 0 }} 份资料
            </div>
            <el-input
              v-model="searchText"
              :prefix-icon="Search"
              clearable
              placeholder="搜索资料名称"
              class="document-search"
            />
          </div>

          <el-table v-if="selectedLibrary" :data="filteredDocuments" stripe>
            <el-table-column label="资料名称" min-width="260">
              <template #default="{ row }">
                <div class="document-name-cell">
                  <span class="document-type-icon"><el-icon><FolderOpen /></el-icon></span>
                  <div>
                    <strong>{{ row.name }}</strong>
                    <small>{{ row.type || "企业资料" }}</small>
                  </div>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="文件类型" width="120">
              <template #default="{ row }">{{ row.type || "文本资料" }}</template>
            </el-table-column>
            <el-table-column label="文件大小" width="120">
              <template #default="{ row }">{{ row.size ? `${Math.ceil(row.size / 1024)} KB` : "—" }}</template>
            </el-table-column>
            <el-table-column prop="savedAt" label="添加时间" width="180">
              <template #default="{ row }">{{ row.savedAt || "2026-09-04" }}</template>
            </el-table-column>
            <el-table-column label="状态" width="100">
              <template #default><el-tag type="success" size="small">已入库</el-tag></template>
            </el-table-column>
            <el-table-column label="操作" width="145" fixed="right">
              <template #default="{ row }">
                <el-button :icon="Edit3" link type="primary" @click="editDocument(row.name)">编辑</el-button>
                <el-button :icon="Trash2" link type="danger" @click="deleteDocument(row.name)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
          <el-empty v-else description="暂无资料库" />
        </el-card>
      </el-col>
    </el-row>

    <el-dialog v-model="dialogVisible" title="添加资料" width="560px" destroy-on-close>
      <div class="add-dialog-context">
        <span>添加到</span>
        <el-tag type="primary" effect="plain">{{ selectedLibrary?.name || "当前资料库" }}</el-tag>
      </div>

      <el-input v-model="libraryForm.name" placeholder="资料名称（可选）" class="field" />
      <label class="dialog-upload">
        <input class="hidden-file-input" type="file" @change="onFileChange" />
        <el-icon><Upload /></el-icon>
        <span>{{ selectedFileName || "选择文件上传" }}</span>
        <small>支持 PDF、DOC、DOCX、TXT 等常用格式</small>
      </label>
      <el-input
        v-model="libraryForm.text"
        :rows="8"
        type="textarea"
        placeholder="也可以直接粘贴文本资料"
      />

      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button :icon="Upload" :loading="busy" type="primary" @click="saveDocument">保存到资料库</el-button>
      </template>
    </el-dialog>
  </div>
</template>
