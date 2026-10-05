import client from './client'

export interface Employee {
  emp_id: string
  name: string
  role: 'employee' | 'admin'
  department?: string
}

export const authApi = {
  /** 登录页一键身份切换：列出员工（演示环境） */
  employees: () => client.get<{ employees: Employee[] }>('/auth/employees'),
  /** 一键登录：{emp_id} → JWT */
  login: (emp_id: string) =>
    client.post<{ access_token: string; emp: Employee }>('/auth/login', { emp_id }),
}
