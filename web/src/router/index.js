import {
  Building2,
  CircleCheckBig,
  FileText,
  FolderGit2,
  ListChecks,
  ChartColumn,
  Settings,
  SquareTerminal,
} from '@lucide/vue'
import { createRouter, createWebHistory } from 'vue-router'

// One entry per page. Entries without an icon, or marked hidden, are reachable but stay out of the sidebar.
export const pages = [
  {
    path: '/',
    name: 'office',
    title: 'Office',
    icon: Building2,
    component: () => import('@/pages/OfficePage.vue'),
  },
  {
    path: '/projects',
    name: 'projects',
    title: 'Projects',
    icon: FolderGit2,
    component: () => import('@/pages/ProjectsPage.vue'),
  },
  {
    path: '/projects/:id',
    name: 'project-detail',
    title: 'Detail proyek',
    parent: 'projects',
    component: () => import('@/pages/ProjectDetailPage.vue'),
  },
  {
    path: '/tasks',
    name: 'tasks',
    title: 'Tasks',
    icon: ListChecks,
    component: () => import('@/pages/TasksPage.vue'),
  },
  {
    path: '/command',
    name: 'command',
    title: 'Command',
    icon: SquareTerminal,
    component: () => import('@/pages/CommandPage.vue'),
  },
  {
    path: '/documents', // the route keeps its name; only the label changed
    name: 'documents',
    title: 'Artifacts',
    subtitle: 'Dokumen, diagram, dan desain yang dihasilkan tim untuk task ini.',
    icon: FileText,
    component: () => import('@/pages/DocumentsPage.vue'),
  },
  {
    path: '/reports',
    name: 'reports',
    title: 'Reports',
    icon: ChartColumn,
    hidden: true, // still opened from a task's Lihat laporan
    component: () => import('@/pages/ReportsPage.vue'),
  },
  {
    path: '/approvals',
    name: 'approvals',
    title: 'Approvals',
    icon: CircleCheckBig,
    component: () => import('@/pages/ApprovalsPage.vue'),
  },
  {
    path: '/settings',
    name: 'settings',
    title: 'Settings',
    subtitle: 'Tampilan dashboard, model tiap agen, dan dokumen yang dihasilkan tim.',
    icon: Settings,
    component: () => import('@/pages/SettingsPage.vue'),
  },
]

export const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  scrollBehavior: () => ({ top: 0 }),
  routes: [
    ...pages.map(({ path, name, title, subtitle, parent, component }) => ({
      path,
      name,
      component,
      meta: { title, subtitle, parent },
    })),
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})
