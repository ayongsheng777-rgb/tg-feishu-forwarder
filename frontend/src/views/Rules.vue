<template>
  <div class="rules-page">
    <el-card shadow="hover">
      <template #header>
        <div class="card-header">
          <span>转发规则</span>
          <el-button type="primary" @click="openAdd">
            <el-icon><Plus /></el-icon> 添加规则
          </el-button>
        </div>
      </template>

      <el-alert type="info" :closable="false" show-icon style="margin-bottom: 15px">
        规则按顺序评估：所有 include/regex/type 规则必须通过，且不能命中任何 exclude 规则。
        channel_id 为空表示全局规则（适用于所有频道）。
      </el-alert>

      <el-table :data="rules" v-loading="loading" stripe>
        <el-table-column prop="id" label="ID" width="60" />
        <el-table-column prop="name" label="规则名称" />
        <el-table-column label="类型" width="160">
          <template #default="{ row }">
            <el-tag :type="ruleTypeColor(row.rule_type)" size="small">
              {{ ruleTypeLabel(row.rule_type) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="pattern" label="匹配模式" show-overflow-tooltip />
        <el-table-column label="作用范围" width="140">
          <template #default="{ row }">
            {{ row.channel_id ? '频道 #' + row.channel_id : '全局' }}
          </template>
        </el-table-column>
        <el-table-column label="状态" width="80">
          <template #default="{ row }">
            <el-switch v-model="row.enabled" @change="toggleEnabled(row)" />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="150">
          <template #default="{ row }">
            <el-button size="small" @click="openEdit(row)">编辑</el-button>
            <el-button size="small" type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="showForm" :title="editing ? '编辑规则' : '添加规则'" width="550px">
      <el-form :model="form" label-width="100px">
        <el-form-item label="规则名称">
          <el-input v-model="form.name" placeholder="如：只转发含'比特币'的消息" />
        </el-form-item>
        <el-form-item label="规则类型">
          <el-select v-model="form.rule_type" style="width: 100%">
            <el-option label="包含关键词 (include)" value="keyword_include" />
            <el-option label="排除关键词 (exclude)" value="keyword_exclude" />
            <el-option label="正则匹配 (regex)" value="regex" />
            <el-option label="消息类型 (msg_type)" value="msg_type" />
          </el-select>
        </el-form-item>
        <el-form-item label="匹配模式">
          <el-input
            v-model="form.pattern"
            :placeholder="patternPlaceholder"
            type="textarea"
            :rows="2"
          />
        </el-form-item>
        <el-form-item v-if="form.rule_type === 'msg_type'" label="消息类型">
          <el-select v-model="form.msg_type_value" style="width: 100%">
            <el-option label="全部" value="all" />
            <el-option label="文本" value="text" />
            <el-option label="图片" value="photo" />
            <el-option label="视频" value="video" />
            <el-option label="文件" value="document" />
          </el-select>
        </el-form-item>
        <el-form-item label="作用频道">
          <el-select v-model="form.channel_id" placeholder="全局（所有频道）" clearable style="width: 100%">
            <el-option
              v-for="ch in channels"
              :key="ch.id"
              :label="ch.channel_title || ch.channel_id"
              :value="ch.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="form.enabled" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showForm = false">取消</el-button>
        <el-button type="primary" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ruleApi, channelApi } from '../api'
import { ElMessage, ElMessageBox } from 'element-plus'

const rules = ref([])
const channels = ref([])
const loading = ref(false)
const showForm = ref(false)
const editing = ref(null)
const form = ref({
  name: '', rule_type: 'keyword_include', pattern: '',
  msg_type_value: 'all', channel_id: null, enabled: true,
})

const patternPlaceholder = computed(() => {
  switch (form.value.rule_type) {
    case 'keyword_include': return '关键词1, 关键词2, 关键词3 (逗号分隔)'
    case 'keyword_exclude': return '屏蔽词1, 屏蔽词2 (逗号分隔)'
    case 'regex': return '正则表达式，如 \\b(bitcoin|BTC)\\b'
    case 'msg_type': return '消息类型值（使用下拉选择）'
    default: return ''
  }
})

function ruleTypeLabel(t) {
  const map = { keyword_include: '包含关键词', keyword_exclude: '排除关键词', regex: '正则匹配', msg_type: '消息类型' }
  return map[t] || t
}

function ruleTypeColor(t) {
  const map = { keyword_include: 'success', keyword_exclude: 'danger', regex: 'warning', msg_type: 'info' }
  return map[t] || ''
}

async function loadRules() {
  loading.value = true
  try {
    const res = await ruleApi.list()
    rules.value = res.data
  } catch {
    ElMessage.error('加载规则失败')
  } finally {
    loading.value = false
  }
}

async function loadChannels() {
  try {
    const res = await channelApi.list()
    channels.value = res.data
  } catch {
    // ignore
  }
}

function openAdd() {
  editing.value = null
  form.value = { name: '', rule_type: 'keyword_include', pattern: '', msg_type_value: 'all', channel_id: null, enabled: true }
  showForm.value = true
}

function openEdit(row) {
  editing.value = row
  form.value = { ...row }
  showForm.value = true
}

async function save() {
  if (!form.value.name || !form.value.pattern) {
    ElMessage.warning('请填写规则名称和匹配模式')
    return
  }
  try {
    if (editing.value) {
      await ruleApi.update(editing.value.id, form.value)
    } else {
      await ruleApi.create(form.value)
    }
    ElMessage.success('保存成功')
    showForm.value = false
    loadRules()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '保存失败')
  }
}

async function toggleEnabled(row) {
  try {
    await ruleApi.update(row.id, { enabled: row.enabled })
  } catch {
    row.enabled = !row.enabled
    ElMessage.error('操作失败')
  }
}

async function remove(row) {
  try {
    await ElMessageBox.confirm(`确定删除规则 "${row.name}"？`, '提示', { type: 'warning' })
    await ruleApi.delete(row.id)
    ElMessage.success('已删除')
    loadRules()
  } catch {
    // cancelled
  }
}

onMounted(() => {
  loadRules()
  loadChannels()
})
</script>

<style scoped>
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
</style>
