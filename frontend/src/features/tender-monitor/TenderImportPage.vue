<script setup lang="ts">
import { ref } from "vue";
import { Upload } from "lucide-vue-next";

defineProps<{
  busy: boolean;
  importForm: {
    name: string;
    text: string;
  };
}>();

const emit = defineEmits<{
  import: [];
  fileSelected: [file: File | null];
}>();

const fileName = ref("");

function onFileChange(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0] || null;
  fileName.value = file?.name || "";
  emit("fileSelected", file);
}
</script>

<template>
  <el-row :gutter="16">
    <el-col :xs="24" :lg="12">
      <el-card class="panel" shadow="never">
        <template #header><span>导入本地招标文件</span></template>
        <el-input v-model="importForm.name" placeholder="文件名（可选）" class="field" />
        <input class="file-input" type="file" @change="onFileChange" />
        <p v-if="fileName" class="file-name">{{ fileName }}</p>
        <el-input
          v-model="importForm.text"
          :rows="12"
          type="textarea"
          placeholder="也可以直接粘贴招标文本"
          class="field"
        />
        <div class="action-row">
          <el-button :icon="Upload" :loading="busy" type="primary" @click="emit('import')">导入并分析</el-button>
        </div>
      </el-card>
    </el-col>

    <el-col :xs="24" :lg="12">
      <el-card class="panel" shadow="never">
        <template #header><span>资料来源说明</span></template>
        <p class="card-note">当前版本支持把可复制文字的文件导入为招标项目，并立即进入信息抽取与匹配流程。</p>
        <el-alert
          title="如果后续要接 OCR、PDF、DOCX、图片识别，这一块会继续扩展到 FastAPI 的文档解析模块。"
          type="info"
          show-icon
        />
      </el-card>
    </el-col>
  </el-row>
</template>
