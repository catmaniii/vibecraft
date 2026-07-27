// RoomLobby 组件单测（Task 9 多人 lobby 视图）
// 覆盖：基础渲染 / 退出房间 / 空位行房主电脑 / 换位 / realtime 开关移除
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import RoomLobby from '@/components/RoomLobby.vue'
import type { RoomStateFrame } from '@/types'

/** 构造测试用 room_state 帧：1 房主（pid_host）+ 1 客人（pid_guest）+ 2 空位。 */
function mkRoomState(overrides: Partial<RoomStateFrame> = {}): RoomStateFrame {
  return {
    type: 'room_state',
    state: 'lobby',
    map: 'DaybreakLE',
    host_player_id: 'pid_host',
    match_id: '',
    realtime: true,
    slots: [
      {
        index: 0, kind: 'bot', team: 1, race: 'Protoss', difficulty: 'VeryHard',
        player_id: 'pid_host', name: '房主', ready: false,
      },
      {
        index: 1, kind: 'bot', team: 2, race: 'Zerg', difficulty: 'VeryHard',
        player_id: 'pid_guest', name: '客人', ready: false,
      },
      {
        index: 2, kind: 'open', team: 1, race: 'Protoss', difficulty: 'VeryHard',
        player_id: '', name: '', ready: false,
      },
      {
        index: 3, kind: 'open', team: 1, race: 'Protoss', difficulty: 'VeryHard',
        player_id: '', name: '', ready: false,
      },
    ],
    ...overrides,
  }
}

/** 1 房主 + 2 空位（canAddComputer=true）。 */
function mkRoomStateSolo(): RoomStateFrame {
  return {
    type: 'room_state',
    state: 'lobby',
    map: 'DaybreakLE',
    host_player_id: 'pid_host',
    match_id: '',
    realtime: false,
    slots: [
      {
        index: 0, kind: 'bot', team: 1, race: 'Protoss', difficulty: 'VeryHard',
        player_id: 'pid_host', name: '房主', ready: false,
      },
      {
        index: 1, kind: 'open', team: 2, race: 'Protoss', difficulty: 'VeryHard',
        player_id: '', name: '', ready: false,
      },
      {
        index: 2, kind: 'open', team: 1, race: 'Protoss', difficulty: 'VeryHard',
        player_id: '', name: '', ready: false,
      },
    ],
  }
}

describe('RoomLobby', () => {
  it('渲染所有 slot 的名字', () => {
    const wrapper = mount(RoomLobby, {
      props: {
        roomState: mkRoomState(),
        myPlayerId: 'pid_host',
        roomError: null,
      },
    })
    expect(wrapper.text()).toContain('房主')
    expect(wrapper.text()).toContain('客人')
  })

  it('非房主看不到房主专属按钮（开始对局）', () => {
    const wrapper = mount(RoomLobby, {
      props: {
        roomState: mkRoomState(),
        myPlayerId: 'pid_guest',  // 客人，非房主
        roomError: null,
      },
    })
    // 非房主不见 [开始对局] 按钮
    expect(wrapper.find('[data-testid="start-game-btn"]').exists()).toBe(false)
    // 非房主不见 [+ 电脑] 按钮
    expect(wrapper.find('[data-testid="add-computer-btn"]').exists()).toBe(false)
  })

  it('自己那行点 [准备] 发出 lobby_ready 帧（ready=true）', async () => {
    // 非房主客人才会看到 [准备] 按钮（#3 房主无准备按钮）
    const wrapper = mount(RoomLobby, {
      props: {
        roomState: mkRoomState(),  // 客人 ready=false → 点后应发 {ready: true}
        myPlayerId: 'pid_guest',
        roomError: null,
      },
    })
    const readyBtn = wrapper.find('[data-testid="ready-btn"]')
    expect(readyBtn.exists()).toBe(true)
    await readyBtn.trigger('click')
    const emitted = wrapper.emitted('lobby') as object[][] | undefined
    expect(emitted).toBeTruthy()
    expect(emitted![0][0]).toEqual({ type: 'lobby_ready', ready: true })
  })

  it('双真人时空位行不显示 [+ 电脑] 按钮（引擎限制 canAddComputer=false）', () => {
    // mkRoomState() 已有 2 个 bot slot → canAddComputer = false
    const wrapper = mount(RoomLobby, {
      props: {
        roomState: mkRoomState(),
        myPlayerId: 'pid_host',
        roomError: null,
      },
    })
    // 空位行存在，但 [+ 电脑] 按钮应不渲染（v-if="isHost && canAddComputer"）
    expect(wrapper.find('[data-testid="add-computer-btn"]').exists()).toBe(false)
  })

  // ---- B: 退出房间 ----

  it('[退出房间] 按钮点击发出 leave 事件', async () => {
    const wrapper = mount(RoomLobby, {
      props: {
        roomState: mkRoomState(),
        myPlayerId: 'pid_host',
        roomError: null,
      },
    })
    const leaveBtn = wrapper.find('[data-testid="leave-room-btn"]')
    expect(leaveBtn.exists()).toBe(true)
    await leaveBtn.trigger('click')
    expect(wrapper.emitted('leave')).toBeTruthy()
  })

  it('非房主也能看到 [退出房间] 按钮', () => {
    const wrapper = mount(RoomLobby, {
      props: {
        roomState: mkRoomState(),
        myPlayerId: 'pid_guest',
        roomError: null,
      },
    })
    expect(wrapper.find('[data-testid="leave-room-btn"]').exists()).toBe(true)
  })

  // ---- C: 空位行内房主 [+ 电脑] ----

  it('房主且 canAddComputer 时，空位行显示 [+ 电脑] 按钮', () => {
    // mkRoomStateSolo: 1 bot → canAddComputer=true
    const wrapper = mount(RoomLobby, {
      props: {
        roomState: mkRoomStateSolo(),
        myPlayerId: 'pid_host',
        roomError: null,
      },
    })
    const addBtns = wrapper.findAll('[data-testid="add-computer-btn"]')
    // 2 个空位行，每个都应有 [+ 电脑] 按钮
    expect(addBtns.length).toBeGreaterThanOrEqual(1)
  })

  it('点击 [+ 电脑] 展开并点击 [添加] 发出带 index 的 lobby_add_computer 帧', async () => {
    const wrapper = mount(RoomLobby, {
      props: {
        roomState: mkRoomStateSolo(),
        myPlayerId: 'pid_host',
        roomError: null,
      },
    })
    // 点第一个 [+ 电脑] 按钮（index=1 的空位行）
    const addBtn = wrapper.find('[data-testid="add-computer-btn"]')
    expect(addBtn.exists()).toBe(true)
    await addBtn.trigger('click')

    // 应展开种族/难度选择
    const form = wrapper.find('[data-testid="add-computer-form"]')
    expect(form.exists()).toBe(true)

    // 点 [添加]
    const confirmBtn = wrapper.find('[data-testid="add-computer-confirm"]')
    await confirmBtn.trigger('click')

    const emitted = wrapper.emitted('lobby') as object[][] | undefined
    expect(emitted).toBeTruthy()
    const frame = emitted![0][0] as Record<string, unknown>
    expect(frame.type).toBe('lobby_add_computer')
    expect(typeof frame.index).toBe('number')
  })

  // ---- D: 点击空位换位 ----

  it('自己已在房间，点击空位行发出 lobby_take_slot（带 index）', async () => {
    // pid_host 在 slot 0；点 slot 1（open）换位
    const wrapper = mount(RoomLobby, {
      props: {
        roomState: mkRoomStateSolo(),
        myPlayerId: 'pid_host',
        roomError: null,
      },
    })
    const openRows = wrapper.findAll('[data-testid="open-slot-row"]')
    expect(openRows.length).toBeGreaterThanOrEqual(1)
    await openRows[0].trigger('click')

    const emitted = wrapper.emitted('lobby') as object[][] | undefined
    expect(emitted).toBeTruthy()
    const frame = emitted![0][0] as Record<string, unknown>
    expect(frame.type).toBe('lobby_take_slot')
    expect(typeof frame.index).toBe('number')
  })

  it('自己不在房间（旁观者），点击空位行不发 lobby_take_slot', async () => {
    // 使用 pid_spectator（不在任何 slot）
    const wrapper = mount(RoomLobby, {
      props: {
        roomState: mkRoomStateSolo(),
        myPlayerId: 'pid_spectator',
        roomError: null,
      },
    })
    const openRows = wrapper.findAll('[data-testid="open-slot-row"]')
    await openRows[0].trigger('click')

    // 没有 lobby_take_slot 帧（旁观者 mySlot=null，click guard 不发）
    const emitted = wrapper.emitted('lobby')
    const hasSwitch = (emitted ?? []).some(
      (args) => (args[0] as Record<string, unknown>).type === 'lobby_take_slot'
    )
    expect(hasSwitch).toBe(false)
  })

  // ---- E: realtime 开关已移除 ----

  it('realtime 开关 UI 不再渲染', () => {
    const wrapper = mount(RoomLobby, {
      props: {
        roomState: mkRoomState(),
        myPlayerId: 'pid_host',
        roomError: null,
      },
    })
    expect(wrapper.find('[data-testid="realtime-toggle"]').exists()).toBe(false)
  })

  // ---- F: #1 自己那行高亮 + #3 房主免准备 ----

  it('#1 自己那行显示 (我) 标签', () => {
    const wrapper = mount(RoomLobby, {
      props: {
        roomState: mkRoomState(),
        myPlayerId: 'pid_host',
        roomError: null,
      },
    })
    expect(wrapper.text()).toContain('(我)')
  })

  it('#3 房主行（自己是房主）不显示 [准备] 按钮', () => {
    const wrapper = mount(RoomLobby, {
      props: {
        roomState: mkRoomState(),
        myPlayerId: 'pid_host',   // 自己是房主
        roomError: null,
      },
    })
    // 房主自己的 row 不应有 ready-btn（#3 房主免准备）
    expect(wrapper.find('[data-testid="ready-btn"]').exists()).toBe(false)
    // 应有金色房主徽标
    expect(wrapper.find('[data-testid="host-badge"]').exists()).toBe(true)
  })

  it('#3 只有房主+电脑时开始按钮可用（非房主玩家空集，allNonHostHumansReady=true）', () => {
    // host + 1 computer（无其他真人）
    const stateHostPlusComputer: RoomStateFrame = {
      type: 'room_state',
      state: 'lobby',
      map: 'DaybreakLE',
      host_player_id: 'pid_host',
      match_id: '',
      realtime: false,
      slots: [
        {
          index: 0, kind: 'bot', team: 1, race: 'Protoss', difficulty: 'VeryHard',
          player_id: 'pid_host', name: '房主', ready: false,
        },
        {
          index: 1, kind: 'computer', team: 2, race: 'Zerg', difficulty: 'VeryHard',
          player_id: '', name: '电脑', ready: false,
        },
      ],
    }
    const wrapper = mount(RoomLobby, {
      props: { roomState: stateHostPlusComputer, myPlayerId: 'pid_host', roomError: null },
    })
    const btn = wrapper.find('[data-testid="start-game-btn"]')
    expect(btn.exists()).toBe(true)
    // 按钮应可用（disabled 属性不存在）
    expect((btn.element as HTMLButtonElement).disabled).toBe(false)
  })

  it('#3 非房主未准备时开始按钮不可用', () => {
    // mkRoomState: host + guest(ready=false) + 2 open slots
    const wrapper = mount(RoomLobby, {
      props: {
        roomState: mkRoomState(),   // guest.ready = false
        myPlayerId: 'pid_host',
        roomError: null,
      },
    })
    const btn = wrapper.find('[data-testid="start-game-btn"]')
    expect(btn.exists()).toBe(true)
    expect((btn.element as HTMLButtonElement).disabled).toBe(true)
  })
})
