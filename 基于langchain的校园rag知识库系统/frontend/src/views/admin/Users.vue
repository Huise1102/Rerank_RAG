<template>
  <div class="page users-page">
    <div class="page-header">
      <div>
        <div class="muted">角色分为系统管理员、部门管理员、教职工和学生，禁用后已登录会话立即失效</div>
      </div>
      <el-button type="primary" @click="openCreate">新建用户</el-button>
    </div>

    <section class="panel">
      <div class="panel-head">
        <div class="panel-title">
          用户列表
          <span class="count">共 {{ total }} 人</span>
        </div>
      </div>

      <div class="filter-bar">
        <el-input v-model="filters.keyword" placeholder="用户名或姓名" clearable style="width: 200px" @keyup.enter="search" />
        <el-select v-model="filters.role" placeholder="角色" clearable style="width: 150px" @change="search">
          <el-option v-for="(label, value) in roleLabels" :key="value" :label="label" :value="value" />
        </el-select>
        <el-select v-model="filters.department_id" placeholder="部门" clearable style="width: 150px" @change="search">
          <el-option v-for="item in departments" :key="item.id" :label="item.name" :value="item.id" />
        </el-select>
        <el-select v-model="filters.status" placeholder="状态" clearable style="width: 130px" @change="search">
          <el-option label="启用" value="active" />
          <el-option label="禁用" value="disabled" />
        </el-select>
        <el-button type="primary" @click="search">查询</el-button>
      </div>

      <el-table :data="items" size="small" border max-height="480">
      <el-table-column prop="username" label="用户名" min-width="150" />
      <el-table-column prop="display_name" label="姓名" min-width="140" />
      <el-table-column label="角色" width="110">
        <template #default="{ row }">
          <el-tag size="small" :type="roleType(row.role)">{{ roleLabels[row.role] || row.role }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="部门" min-width="160">
        <template #default="{ row }">{{ departmentName(row) }}</template>
      </el-table-column>
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag size="small" :type="row.status === 'active' ? 'success' : 'info'">
            {{ row.status === 'active' ? '启用' : '禁用' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="最近登录" width="170">
        <template #default="{ row }">{{ formatTime(row.last_login_at) || '从未登录' }}</template>
      </el-table-column>
      <el-table-column label="操作" width="250">
        <template #default="{ row }">
          <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
          <el-button link type="primary" size="small" @click="openReset(row)">重置密码</el-button>
          <el-button
            v-if="row.status === 'active' && row.id !== auth.user?.id"
            link
            type="danger"
            size="small"
            @click="disable(row)"
          >
            禁用
          </el-button>
          <el-button
            v-if="row.id !== auth.user?.id"
            link
            type="danger"
            size="small"
            @click="close(row)"
          >
            注销
          </el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-pagination
      v-model:current-page="page"
      :page-size="pageSize"
      :total="total"
      layout="total, prev, pager, next"
      style="margin-top: 12px"
      @current-change="load"
    />
    </section>

    <el-dialog v-model="createDialog" title="新建用户" width="480px">
      <el-form :model="createForm" label-position="top">
        <el-form-item label="用户名" required>
          <el-input v-model="createForm.username" placeholder="仅字母、数字、下划线、点、横线" />
        </el-form-item>
        <el-form-item label="姓名" required>
          <el-input v-model="createForm.display_name" />
        </el-form-item>
        <el-form-item label="初始密码" required>
          <el-input v-model="createForm.password" placeholder="至少 8 位，包含字母和数字" />
        </el-form-item>
        <el-form-item label="角色">
          <el-select v-model="createForm.role" style="width: 100%">
            <el-option v-for="(label, value) in roleLabels" :key="value" :label="label" :value="value" />
          </el-select>
        </el-form-item>
        <el-form-item label="部门">
          <el-select v-model="createForm.department_id" clearable style="width: 100%">
            <el-option v-for="item in departments" :key="item.id" :label="item.name" :value="item.id" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="create">创建</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="editDialog" title="编辑用户" width="480px">
      <el-form :model="editForm" label-position="top">
        <el-form-item label="姓名">
          <el-input v-model="editForm.display_name" />
        </el-form-item>
        <el-form-item label="角色">
          <el-select v-model="editForm.role" style="width: 100%">
            <el-option v-for="(label, value) in roleLabels" :key="value" :label="label" :value="value" />
          </el-select>
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="editForm.status" style="width: 100%">
            <el-option label="启用" value="active" />
            <el-option label="禁用" value="disabled" />
          </el-select>
        </el-form-item>
        <el-form-item label="部门">
          <el-select v-model="editForm.department_id" clearable style="width: 100%">
            <el-option v-for="item in departments" :key="item.id" :label="item.name" :value="item.id" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveEdit">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="resetDialog" title="重置密码" width="460px">
      <el-form label-position="top">
        <el-form-item label="新密码">
          <el-input v-model="resetPasswordValue" placeholder="留空则随机生成" />
        </el-form-item>
        <div class="muted">重置后该用户所有登录会话失效，下次登录需修改密码</div>
      </el-form>
      <template #footer>
        <el-button @click="resetDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="doReset">重置</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onActivated, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  closeUser as closeUserApi,
  createUser,
  disableUser,
  listDepartments,
  listUsers,
  resetPassword,
  updateUser
} from '../../api/admin'
import { useAuthStore } from '../../stores/auth'

const auth = useAuthStore()
const roleLabels = {
  admin: '系统管理员',
  dept_admin: '部门管理员',
  staff: '教职工',
  student: '学生'
}
const departments = ref([])
const items = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = 20
const filters = reactive({ keyword: '', role: '', department_id: '', status: '' })
const createDialog = ref(false)
const editDialog = ref(false)
const resetDialog = ref(false)
const saving = ref(false)
const editing = ref(null)
const resetting = ref(null)
const resetPasswordValue = ref('')
const createForm = reactive({
  username: '',
  display_name: '',
  password: '',
  role: 'student',
  department_id: null
})
const editForm = reactive({ display_name: '', role: 'student', status: 'active', department_id: null })

function roleType(role) {
  return { admin: 'danger', dept_admin: 'warning', staff: 'primary', student: 'info' }[role] || 'info'
}

function departmentName(row) {
  if (row.department_id) {
    const matched = departments.value.find((item) => item.id === row.department_id)
    if (matched) {
      return matched.name
    }
  }
  return row.department || '未分配'
}

function formatTime(value) {
  return value ? String(value).replace('T', ' ').slice(0, 16) : ''
}

async function load() {
  const data = await listUsers({
    page: page.value,
    page_size: pageSize,
    keyword: filters.keyword || undefined,
    role: filters.role || undefined,
    department_id: filters.department_id || undefined,
    status: filters.status || undefined
  })
  items.value = data.items
  total.value = data.total
}

function search() {
  page.value = 1
  load()
}

function openCreate() {
  Object.assign(createForm, {
    username: '',
    display_name: '',
    password: '',
    role: 'student',
    department_id: null
  })
  createDialog.value = true
}

async function create() {
  if (!createForm.username || !createForm.display_name || !createForm.password) {
    ElMessage.warning('请填写用户名、姓名和初始密码')
    return
  }
  saving.value = true
  try {
    await createUser({ ...createForm })
    ElMessage.success('用户已创建，首次登录需修改密码')
    createDialog.value = false
    await load()
  } finally {
    saving.value = false
  }
}

function openEdit(row) {
  editing.value = row
  Object.assign(editForm, {
    display_name: row.display_name,
    role: row.role,
    status: row.status,
    department_id: row.department_id || null
  })
  editDialog.value = true
}

async function saveEdit() {
  saving.value = true
  try {
    await updateUser(editing.value.id, { ...editForm })
    ElMessage.success('用户已更新')
    editDialog.value = false
    await load()
  } finally {
    saving.value = false
  }
}

function openReset(row) {
  resetting.value = row
  resetPasswordValue.value = ''
  resetDialog.value = true
}

async function doReset() {
  saving.value = true
  try {
    const result = await resetPassword(resetting.value.id, {
      new_password: resetPasswordValue.value || null
    })
    resetDialog.value = false
    await ElMessageBox.alert(
      `临时密码：${result.temporary_password}\n请转交用户，并要求首次登录后修改。`,
      '重置成功'
    )
    await load()
  } finally {
    saving.value = false
  }
}

async function disable(row) {
  try {
    await ElMessageBox.confirm(`确认禁用用户「${row.username}」？`, '禁用用户', { type: 'warning' })
  } catch {
    return
  }
  await disableUser(row.id)
  ElMessage.success('用户已禁用')
  await load()
}

async function close(row) {
  try {
    await ElMessageBox.confirm(
      `注销后「${row.username}」的账号、会话与通知将被永久删除且无法恢复，确认注销？`,
      '注销用户',
      { type: 'warning', confirmButtonText: '确认注销' }
    )
  } catch {
    return
  }
  try {
    await closeUserApi(row.id)
    ElMessage.success('用户已注销')
    if (items.value.length === 1 && page.value > 1) {
      page.value -= 1
    }
    await load()
  } catch {
    // 失败原因由请求拦截器统一提示
  }
}

onActivated(async () => {
  departments.value = await listDepartments()
  await load()
})
</script>

<style scoped>
.users-page {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.panel {
  background: #fff;
  border: 1px solid #ebeef5;
  border-radius: 10px;
  padding: 14px 16px;
}

.panel-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}

.panel-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  font-weight: 600;
  color: #303133;
}

.panel-title::before {
  content: '';
  width: 3px;
  height: 14px;
  border-radius: 2px;
  background: #0f9d8f;
}

.count {
  font-size: 12px;
  font-weight: 400;
  color: #9ca3af;
}

.filter-bar {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}
</style>
