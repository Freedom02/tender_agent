<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { Bell, CheckCircle2, CloudCog, Database, Globe2, KeyRound, Pencil, Play, Plus, Save, Search, Trash2, Zap } from "lucide-vue-next";
import { ElMessage } from "element-plus";
import type { MonitoringStatus } from "../../shared/types";

const props = defineProps<{
  searchForm: {
    query: string;
    region: string;
    type: string;
    currentOnly: boolean;
  };
  config: Record<string, any>;
  monitoring: MonitoringStatus;
  busy: boolean;
}>();

const includeKeywords = ref("数字孪生, 数据智能, AI 智能体");
const excludeKeywords = ref("");
const serviceType = ref("数字孪生");
const region = ref("全国");
const targetDirections = ref<string[]>([]);
const targetScenarios = ref<string[]>([]);
const targetProducts = ref<string[]>([]);
const targetCapabilities = ref<string[]>([]);
const synonyms = ref("");
const targetProcurementTypes = ref("");
const lookbackDays = ref(7);
const frequency = ref("30");
const monitorEnabled = ref(true);
const showApiKey = ref(false);
const apiProvider = ref("企业统一 API");
const apiBaseUrl = ref("https://api.openai.com/v1");
const apiKey = ref("");
const apiModel = ref("gpt-4o-mini");
const organizationId = ref("");
const aiConfigured = computed(() => props.config.ai?.configured === true);

const monitorSites = computed(() => props.monitoring.sites);
const inAppEnabled = ref(true);
const wecomEnabled = ref(false);
const wecomWebhook = ref("");
const serviceTypeOptions = ["数字孪生", "数据智能", "企业架构", "AI 智能体", "智慧园区", "智慧城市", "数据中心与运维", "低空经济", "电力与能源", "智慧矿山", "三维可视化", "软件开发"];
const regionOptions = ["全国", "北京市", "上海市", "广东省", "湖北省", "四川省", "浙江省", "江苏省", "河南省", "成都市", "深圳市", "广州市"];
const siteDialogVisible = ref(false);
const editingSiteId = ref("");
const siteForm = ref({
  name: "",
  url: "",
  listUrl: "",
  fetchMode: "html",
  itemSelector: "",
  titleSelector: "",
  dateSelector: "",
  linkSelector: "",
  regionSelector: "",
  categorySelector: "",
  contentSelector: "",
  jsonItemsPath: "",
  jsonTitlePath: "title",
  jsonLinkPath: "url",
  jsonDatePath: "publishedDate",
  jsonRegionPath: "region",
  jsonCategoryPath: "category",
  jsonContentPath: "summary"
});

function loadSavedConfig() {
  const monitoring = props.config.monitoring || {};
  const notifications = props.config.notifications || {};
  const wecom = props.config.wecom || {};
  serviceType.value = monitoring.serviceType || serviceType.value;
  region.value = monitoring.region || region.value;
  targetDirections.value = [...(monitoring.targetDirections || [])];
  targetScenarios.value = [...(monitoring.targetScenarios || [])];
  targetProducts.value = [...(monitoring.targetProducts || [])];
  targetCapabilities.value = [...(monitoring.targetCapabilities || [])];
  synonyms.value = (monitoring.synonyms || []).join(", ");
  targetProcurementTypes.value = (monitoring.targetProcurementTypes || []).join(", ");
  lookbackDays.value = Number(monitoring.lookbackDays || lookbackDays.value);
  includeKeywords.value = (monitoring.includeKeywords || [includeKeywords.value]).join(", ");
  excludeKeywords.value = (monitoring.excludeKeywords || []).join(", ");
  frequency.value = String(monitoring.frequencyMinutes || frequency.value);
  monitorEnabled.value = monitoring.enabled !== false;
  inAppEnabled.value = notifications.inAppEnabled !== false;
  wecomEnabled.value = notifications.wecomEnabled === true;
  wecomWebhook.value = wecom.configured ? "已配置" : "";
  apiProvider.value = props.config.ai?.provider || apiProvider.value;
  apiBaseUrl.value = props.config.ai?.baseUrl || apiBaseUrl.value;
  apiModel.value = props.config.ai?.model || apiModel.value;
  organizationId.value = props.config.ai?.organizationId || organizationId.value;
  apiKey.value = "";
}

function suggestOptions(options: string[], keyword: string, callback: (items: { value: string }[]) => void) {
  const query = keyword.trim().toLowerCase();
  callback(
    options
      .filter((item) => !query || item.toLowerCase().includes(query))
      .map((item) => ({ value: item }))
  );
}

function suggestServiceTypes(keyword: string, callback: (items: { value: string }[]) => void) {
  suggestOptions(serviceTypeOptions, keyword, callback);
}

function suggestRegions(keyword: string, callback: (items: { value: string }[]) => void) {
  suggestOptions(regionOptions, keyword, callback);
}

watch(
  () => props.config,
  loadSavedConfig,
  { deep: true, immediate: true }
);

function currentAiConfig() {
  return {
    provider: apiProvider.value,
    baseUrl: apiBaseUrl.value.trim(),
    apiKey: apiKey.value.trim(),
    model: apiModel.value.trim(),
    organizationId: organizationId.value.trim()
  };
}

function saveAllConfig() {
  emit("saveConfig", {
    monitoring: {
      enabled: monitorEnabled.value,
      serviceType: serviceType.value,
      region: region.value,
      targetDirections: targetDirections.value,
      targetScenarios: targetScenarios.value,
      targetProducts: targetProducts.value,
      targetCapabilities: targetCapabilities.value,
      synonyms: synonyms.value.split(/[,，;；、]/).map((item) => item.trim()).filter(Boolean),
      targetProcurementTypes: targetProcurementTypes.value.split(/[,，;；、]/).map((item) => item.trim()).filter(Boolean),
      includeKeywords: includeKeywords.value.split(/[,，]/).map((item) => item.trim()).filter(Boolean),
      excludeKeywords: excludeKeywords.value.split(/[,，]/).map((item) => item.trim()).filter(Boolean),
      frequencyMinutes: Number(frequency.value),
      lookbackDays: lookbackDays.value
    },
    notifications: {
      inAppEnabled: inAppEnabled.value,
      wecomEnabled: wecomEnabled.value
    },
    wecom: {
      webhook: wecomWebhook.value === "已配置" ? "" : wecomWebhook.value
    },
    ai: currentAiConfig()
  });
}

function testConnection() {
  emit("testAi", currentAiConfig());
}

const emit = defineEmits<{
  saveConfig: [config: Record<string, unknown>];
  runSite: [siteId: string];
  updateSite: [{ id: string; enabled: boolean }];
  createSite: [payload: Record<string, unknown>];
  updateSiteConfig: [{ id: string; payload: Record<string, unknown> }];
  deleteSite: [siteId: string];
  testSite: [siteId: string];
  testInApp: [];
  testWecom: [];
  testAi: [ai: Record<string, unknown>];
}>();

function openCreateSite() {
  editingSiteId.value = "";
  siteForm.value = {
    name: "",
    url: "",
    listUrl: "",
    fetchMode: "html",
    itemSelector: "",
    titleSelector: "",
    dateSelector: "",
    linkSelector: "",
    regionSelector: "",
    categorySelector: "",
    contentSelector: "",
    jsonItemsPath: "",
    jsonTitlePath: "title",
    jsonLinkPath: "url",
    jsonDatePath: "publishedDate",
    jsonRegionPath: "region",
    jsonCategoryPath: "category",
    jsonContentPath: "summary"
  };
  siteDialogVisible.value = true;
}

function openEditSite(site: any) {
  const selectors = site.selectors || {};
  const jsonFields = site.jsonFields || {};
  editingSiteId.value = site.id;
  siteForm.value = {
    name: site.name || "",
    url: site.url || "",
    listUrl: site.listUrl || site.url || "",
    fetchMode: site.fetchMode || "html",
    itemSelector: selectors.item || "",
    titleSelector: selectors.title || "",
    dateSelector: selectors.date || "",
    linkSelector: selectors.link || "",
    regionSelector: selectors.region || "",
    categorySelector: selectors.category || "",
    contentSelector: selectors.content || "",
    jsonItemsPath: jsonFields.items || "",
    jsonTitlePath: jsonFields.title || "title",
    jsonLinkPath: jsonFields.link || "url",
    jsonDatePath: jsonFields.date || "publishedDate",
    jsonRegionPath: jsonFields.region || "region",
    jsonCategoryPath: jsonFields.category || "category",
    jsonContentPath: jsonFields.content || "summary"
  };
  siteDialogVisible.value = true;
}

function submitSite() {
  const value = siteForm.value;
  const payload = {
    name: value.name.trim(),
    url: value.url.trim(),
    listUrl: value.listUrl.trim() || value.url.trim(),
    fetchMode: value.fetchMode,
    selectors: {
      item: value.itemSelector.trim(),
      title: value.titleSelector.trim(),
      date: value.dateSelector.trim(),
      link: value.linkSelector.trim(),
      region: value.regionSelector.trim(),
      category: value.categorySelector.trim(),
      content: value.contentSelector.trim()
    },
    jsonFields: {
      items: value.jsonItemsPath.trim(),
      title: value.jsonTitlePath.trim(),
      link: value.jsonLinkPath.trim(),
      date: value.jsonDatePath.trim(),
      region: value.jsonRegionPath.trim(),
      category: value.jsonCategoryPath.trim(),
      content: value.jsonContentPath.trim()
    }
  };
  if (editingSiteId.value) emit("updateSiteConfig", { id: editingSiteId.value, payload });
  else emit("createSite", payload);
  siteDialogVisible.value = false;
}
</script>

<template>
  <div class="automation-page">
    <el-row :gutter="16">
      <el-col :xs="24" :lg="12">
        <el-card class="panel config-card" shadow="never">
          <template #header>
          <div class="panel-header">
              <div class="config-title">
                <el-icon><Search /></el-icon>
                <span>搜索配置</span>
              </div>
              <el-switch v-model="monitorEnabled" active-text="24小时监控" />
            </div>
          </template>

          <el-form label-position="top">
            <el-form-item label="业务服务类型">
              <el-autocomplete
                v-model="serviceType"
                class="full-width"
                :fetch-suggestions="suggestServiceTypes"
                clearable
                :trigger-on-focus="true"
                placeholder="选择或输入业务服务类型"
              />
            </el-form-item>

            <el-alert
              title="本体筛选为空时，系统会使用公司资料库中的全部方向、场景、产品和能力进行语义匹配。"
              type="info"
              :closable="false"
              show-icon
              class="profile-alert"
            />

            <el-form-item label="目标业务方向">
              <el-select v-model="targetDirections" class="full-width" multiple filterable allow-create default-first-option collapse-tags collapse-tags-tooltip clearable placeholder="不限定方向" />
            </el-form-item>

            <el-form-item label="目标行业场景">
              <el-select v-model="targetScenarios" class="full-width" multiple filterable allow-create default-first-option collapse-tags collapse-tags-tooltip clearable placeholder="不限定场景" />
            </el-form-item>

            <el-form-item label="目标产品或服务">
              <el-select v-model="targetProducts" class="full-width" multiple filterable allow-create default-first-option collapse-tags collapse-tags-tooltip clearable placeholder="不限定产品" />
            </el-form-item>

            <el-form-item label="目标技术能力">
              <el-select v-model="targetCapabilities" class="full-width" multiple filterable allow-create default-first-option collapse-tags collapse-tags-tooltip clearable placeholder="不限定能力" />
            </el-form-item>

            <el-form-item label="同义词或业务补充词">
              <el-input v-model="synonyms" placeholder="例如：数字孪生平台, 3D 可视化, DCIM" />
            </el-form-item>

            <el-form-item label="采购方式">
              <el-input v-model="targetProcurementTypes" placeholder="可选，例如：公开招标, 竞争性磋商" />
            </el-form-item>

            <el-form-item label="目标地区">
              <el-autocomplete
                v-model="region"
                class="full-width"
                :fetch-suggestions="suggestRegions"
                clearable
                :trigger-on-focus="true"
                placeholder="选择或输入目标地区"
              />
            </el-form-item>

            <el-form-item label="包含关键词">
              <el-input v-model="includeKeywords" placeholder="多个关键词用逗号分隔" />
            </el-form-item>

            <el-form-item label="排除关键词">
              <el-input v-model="excludeKeywords" placeholder="例如：废标、流标" />
            </el-form-item>

            <el-form-item label="抓取频率">
              <el-radio-group v-model="frequency">
                <el-radio-button label="30">每30分钟</el-radio-button>
                <el-radio-button label="60">每1小时</el-radio-button>
                <el-radio-button label="360">每6小时</el-radio-button>
              </el-radio-group>
            </el-form-item>

            <el-form-item label="公告时间范围">
              <el-input-number v-model="lookbackDays" :min="1" :max="90" controls-position="right" />
              <span class="form-hint inline-hint">抓取最近多少天的公告</span>
            </el-form-item>

            <el-form-item label="提醒方式">
              <div class="notification-options">
                <el-checkbox v-model="inAppEnabled">页面弹窗</el-checkbox>
                <el-checkbox v-model="wecomEnabled">企业微信</el-checkbox>
                <el-button :icon="Bell" link type="primary" @click="emit('testInApp')">测试页面提醒</el-button>
              </div>
            </el-form-item>

            <el-form-item label="企业微信机器人 Webhook">
              <el-input v-model="wecomWebhook" placeholder="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=..." />
              <div class="form-hint">保存后由后端保管；不填写时会保留已配置的地址。</div>
            </el-form-item>
          </el-form>
        </el-card>
      </el-col>

      <el-col :xs="24" :lg="12">
        <el-card class="panel config-card" shadow="never">
          <template #header>
            <div class="panel-header">
              <div class="config-title">
                <el-icon><CloudCog /></el-icon>
                <span>企业 API 接入配置</span>
              </div>
              <el-tag type="success" effect="plain">公司统一账号</el-tag>
            </div>
          </template>

          <el-alert
            title="API 密钥仅用于企业模型服务接入，正式环境建议由后端或密钥管理服务统一保管。"
            type="info"
            show-icon
            :closable="false"
            class="api-alert"
          />

          <el-form label-position="top">
            <el-form-item label="API 服务商">
              <el-select v-model="apiProvider" class="full-width">
                <el-option label="企业统一 API" value="企业统一 API" />
                <el-option label="OpenAI 兼容接口" value="OpenAI 兼容接口" />
                <el-option label="其他模型服务" value="其他模型服务" />
              </el-select>
            </el-form-item>

            <el-form-item label="API Base URL">
              <el-input v-model="apiBaseUrl" placeholder="请输入企业 API 服务地址" />
            </el-form-item>

            <el-form-item label="API Key">
              <el-input
                v-model="apiKey"
                :type="showApiKey ? 'text' : 'password'"
                :placeholder="aiConfigured ? '已配置，留空将保留当前密钥' : '请输入企业 API Key'"
                autocomplete="new-password"
              >
                <template #prefix><el-icon><KeyRound /></el-icon></template>
                <template #suffix>
                  <button class="input-icon-button" type="button" @click="showApiKey = !showApiKey">
                    {{ showApiKey ? "隐藏" : "显示" }}
                  </button>
                </template>
              </el-input>
              <div v-if="aiConfigured" class="form-hint">密钥已由后端保存并脱敏显示；只有重新填写时才会更新。</div>
            </el-form-item>

            <el-row :gutter="12">
              <el-col :xs="24" :md="12">
                <el-form-item label="模型名称">
                  <el-input v-model="apiModel" placeholder="例如：gpt-4o-mini" />
                </el-form-item>
              </el-col>
              <el-col :xs="24" :md="12">
                <el-form-item label="企业项目 ID（可选）">
                  <el-input v-model="organizationId" placeholder="请输入项目或组织 ID" />
                </el-form-item>
              </el-col>
            </el-row>
          </el-form>

          <div class="config-actions">
            <el-button :icon="CheckCircle2" plain @click="testConnection">测试 API</el-button>
            <el-button :icon="Bell" plain @click="emit('testWecom')">测试企业微信</el-button>
            <el-button :icon="Save" :loading="busy" type="primary" @click="saveAllConfig">保存配置</el-button>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-card class="panel site-panel" shadow="never">
      <template #header>
        <div class="panel-header">
          <div class="config-title">
            <el-icon><Globe2 /></el-icon>
            <span>监控网站</span>
          </div>
          <div class="site-header-actions">
            <el-tag type="success" effect="plain">每 {{ frequency }} 分钟抓取</el-tag>
            <el-button :icon="Plus" type="primary" @click="openCreateSite">添加监控网站</el-button>
          </div>
        </div>
      </template>

      <el-table :data="monitorSites" stripe>
        <el-table-column label="网站名称" min-width="220">
          <template #default="{ row }">
            <div class="site-name">
              <span class="site-status-dot" :class="{ paused: !row.enabled }"></span>
              <div>
                <strong>{{ row.name }}</strong>
                <small>{{ row.custom ? `自定义 · ${row.listUrl || row.url}` : row.url }}</small>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="监控状态" width="130">
          <template #default="{ row }">
            <el-switch
              :model-value="row.enabled"
              active-text="启用"
              inactive-text="停用"
              @update:model-value="emit('updateSite', { id: row.id, enabled: $event })"
            />
          </template>
        </el-table-column>
        <el-table-column prop="status" label="最近状态" width="130" />
        <el-table-column prop="latest" label="上次抓取" width="180" />
        <el-table-column prop="next" label="下次抓取" width="160" />
        <el-table-column label="操作" width="130" fixed="right">
          <template #default="{ row }">
            <div class="site-actions">
              <el-button :icon="Play" type="primary" link :disabled="!row.enabled" @click="emit('runSite', row.id)">抓取</el-button>
              <el-button v-if="row.custom" :icon="Zap" type="primary" link @click="emit('testSite', row.id)">测试</el-button>
              <el-dropdown v-if="row.custom" trigger="click">
                <el-button link class="site-more-button">更多</el-button>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item :icon="Pencil" @click="openEditSite(row)">编辑</el-dropdown-item>
                    <el-dropdown-item :icon="Trash2" divided @click="emit('deleteSite', row.id)">删除</el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </div>
          </template>
        </el-table-column>
      </el-table>

      <div class="site-footer">
        <el-icon><Database /></el-icon>
        <span>当前共配置 {{ monitorSites.length }} 个监控网站，启用 {{ monitorSites.filter((site) => site.enabled).length }} 个。</span>
        <span class="form-hint">抓取结果将进入招标项目列表并参与提醒和匹配评估。</span>
      </div>
    </el-card>

    <el-dialog v-model="siteDialogVisible" :title="editingSiteId ? '编辑自定义监控网站' : '添加自定义监控网站'" width="680px">
      <el-alert
        title="系统只访问公开地址，不绕过登录、验证码或访问限制。保存后请先测试，确认解析结果再启用自动抓取。"
        type="info"
        show-icon
        :closable="false"
        class="site-dialog-alert"
      />
      <el-form label-position="top">
        <el-row :gutter="12">
          <el-col :xs="24" :md="12"><el-form-item label="网站名称" required><el-input v-model="siteForm.name" placeholder="例如：某市公共资源交易中心" /></el-form-item></el-col>
          <el-col :xs="24" :md="12"><el-form-item label="抓取方式"><el-select v-model="siteForm.fetchMode" class="full-width"><el-option label="网页列表" value="html" /><el-option label="JSON / API" value="json" /></el-select></el-form-item></el-col>
        </el-row>
        <el-form-item label="网站首页地址" required><el-input v-model="siteForm.url" placeholder="https://example.com" /></el-form-item>
        <el-form-item label="公告列表地址" required><el-input v-model="siteForm.listUrl" placeholder="https://example.com/notice" /><div class="form-hint">网页列表模式填写公告列表页；API 模式填写返回公告数据的接口地址。</div></el-form-item>

        <template v-if="siteForm.fetchMode === 'html'">
          <el-divider content-position="left">网页解析规则（CSS 选择器）</el-divider>
          <el-alert title="只需填写能定位公告的规则；常见写法：.notice-item、.title、a。复杂选择器暂不支持。" type="warning" :closable="false" class="site-dialog-alert" />
          <el-row :gutter="12">
            <el-col :xs="24" :md="12"><el-form-item label="公告条目选择器" required><el-input v-model="siteForm.itemSelector" placeholder="例如：.notice-item" /></el-form-item></el-col>
            <el-col :xs="24" :md="12"><el-form-item label="标题选择器" required><el-input v-model="siteForm.titleSelector" placeholder="例如：.title" /></el-form-item></el-col>
            <el-col :xs="24" :md="12"><el-form-item label="详情链接选择器" required><el-input v-model="siteForm.linkSelector" placeholder="例如：a" /></el-form-item></el-col>
            <el-col :xs="24" :md="12"><el-form-item label="发布日期选择器"><el-input v-model="siteForm.dateSelector" placeholder="例如：.date" /></el-form-item></el-col>
            <el-col :xs="24" :md="12"><el-form-item label="地区选择器"><el-input v-model="siteForm.regionSelector" placeholder="例如：.region" /></el-form-item></el-col>
            <el-col :xs="24" :md="12"><el-form-item label="类型选择器"><el-input v-model="siteForm.categorySelector" placeholder="例如：.category" /></el-form-item></el-col>
          </el-row>
          <el-form-item label="摘要/正文选择器"><el-input v-model="siteForm.contentSelector" placeholder="例如：.summary" /></el-form-item>
        </template>

        <template v-else>
          <el-divider content-position="left">JSON 字段映射</el-divider>
          <el-form-item label="公告数组路径"><el-input v-model="siteForm.jsonItemsPath" placeholder="例如：data.records；留空时自动识别 data / items / results" /></el-form-item>
          <el-row :gutter="12">
            <el-col :xs="24" :md="12"><el-form-item label="标题字段路径" required><el-input v-model="siteForm.jsonTitlePath" placeholder="title" /></el-form-item></el-col>
            <el-col :xs="24" :md="12"><el-form-item label="详情链接字段路径" required><el-input v-model="siteForm.jsonLinkPath" placeholder="url" /></el-form-item></el-col>
            <el-col :xs="24" :md="12"><el-form-item label="日期字段路径"><el-input v-model="siteForm.jsonDatePath" placeholder="publishedDate" /></el-form-item></el-col>
            <el-col :xs="24" :md="12"><el-form-item label="地区字段路径"><el-input v-model="siteForm.jsonRegionPath" placeholder="region" /></el-form-item></el-col>
            <el-col :xs="24" :md="12"><el-form-item label="类型字段路径"><el-input v-model="siteForm.jsonCategoryPath" placeholder="category" /></el-form-item></el-col>
            <el-col :xs="24" :md="12"><el-form-item label="摘要字段路径"><el-input v-model="siteForm.jsonContentPath" placeholder="summary" /></el-form-item></el-col>
          </el-row>
        </template>
      </el-form>
      <template #footer>
        <el-button @click="siteDialogVisible = false">取消</el-button>
        <el-button type="primary" :disabled="!siteForm.name || !siteForm.url || !siteForm.listUrl" @click="submitSite">保存网站</el-button>
      </template>
    </el-dialog>
  </div>
</template>
