import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/LoginView.vue'),
      meta: { public: true },
    },
    {
      path: '/',
      component: () => import('@/components/layout/AppLayout.vue'),
      meta: { requiresAuth: true },
      children: [
        { path: '', redirect: '/chat' },
        {
          path: 'chat',
          name: 'chat',
          component: () => import('@/views/UnifiedChatView.vue'),
        },
        {
          path: 'bookings',
          name: 'bookings',
          component: () => import('@/views/MyBookingsView.vue'),
        },
        {
          path: 'equipment',
          name: 'equipment',
          component: () => import('@/views/EquipmentView.vue'),
        },
        {
          path: 'tickets',
          name: 'tickets',
          component: () => import('@/views/MyTicketsView.vue'),
        },
        {
          path: 'reports',
          name: 'reports',
          component: () => import('@/views/ReportsView.vue'),
          meta: { requiresAdmin: true },
        },
        {
          path: 'messages',
          name: 'messages',
          component: () => import('@/views/MessagesView.vue'),
        },
      ],
    },
    { path: '/:pathMatch(.*)*', redirect: '/chat' },
  ],
})

router.beforeEach((to, _from, next) => {
  const auth = useAuthStore()
  if (to.meta.public) {
    if (auth.isLoggedIn && to.name === 'login') return next('/chat')
    return next()
  }
  if (!auth.isLoggedIn) return next('/login')
  if (to.meta.requiresAdmin && !auth.isAdmin) return next('/chat')
  next()
})

export default router
