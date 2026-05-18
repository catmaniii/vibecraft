// Click-outside 监听器:点击 ref element 之外时触发 callback。
//
// 用途:下拉菜单 / popup,点别的地方自动关闭。
// 替代 @vueuse 的 onClickOutside(项目没装 vueuse)。
//
// 用法:
//   const popup = ref<HTMLElement | null>(null)
//   const open = ref(false)
//   useClickOutside(popup, () => { open.value = false }, () => open.value)

import { onMounted, onBeforeUnmount, type Ref } from 'vue'

export function useClickOutside(
  target: Ref<HTMLElement | null>,
  handler: (event: Event) => void,
  // active 函数:返回 true 时才监听(避免 popup 关闭时也跑 handler)
  active: () => boolean = () => true,
): void {
  function onPointerDown(event: Event) {
    if (!active()) return
    const el = target.value
    if (!el) return
    if (event.target instanceof Node && el.contains(event.target)) return
    handler(event)
  }

  onMounted(() => {
    document.addEventListener('mousedown', onPointerDown, true)
    document.addEventListener('touchstart', onPointerDown, true)
  })

  onBeforeUnmount(() => {
    document.removeEventListener('mousedown', onPointerDown, true)
    document.removeEventListener('touchstart', onPointerDown, true)
  })
}
