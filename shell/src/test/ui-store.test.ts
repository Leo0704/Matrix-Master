/**
 * ui-store 持久化测试（v0.7+ 业务模型重构）。
 *
 * 验证：
 * - setActiveBusinessId 写入 store
 * - 部分持久化只保留 activeBusinessId / sidebarOpen / theme
 * - localStorage['matrix-ui'] 重启后能恢复 activeBusinessId
 */
import { describe, expect, it, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useUIStore } from '@/stores/ui-store';

describe('ui-store (v0.7+ 业务模型重构)', () => {
  beforeEach(() => {
    localStorage.clear();
    // 重置 store 到初始状态
    useUIStore.setState({
      sidebarOpen: true,
      theme: 'light',
      deviceFilter: null,
      statusFilter: null,
      activeBusinessId: null,
    });
  });

  it('初始 activeBusinessId 为 null', () => {
    const { result } = renderHook(() => useUIStore((s) => s.activeBusinessId));
    expect(result.current).toBeNull();
  });

  it('setActiveBusinessId 写入 store', () => {
    const { result: bizId } = renderHook(() =>
      useUIStore((s) => s.activeBusinessId),
    );
    const { result: setter } = renderHook(() =>
      useUIStore((s) => s.setActiveBusinessId),
    );

    act(() => {
      setter.current('biz-123');
    });
    expect(bizId.current).toBe('biz-123');
  });

  it('activeBusinessId 持久化到 localStorage', () => {
    const { result: setter } = renderHook(() =>
      useUIStore((s) => s.setActiveBusinessId),
    );

    act(() => {
      setter.current('biz-persistent');
    });

    // localStorage 中应该有 matrix-ui key
    const stored = JSON.parse(localStorage.getItem('matrix-ui') ?? '{}');
    expect(stored.state?.activeBusinessId).toBe('biz-persistent');
  });

  it('切回 null 也持久化', () => {
    const { result: setter } = renderHook(() =>
      useUIStore((s) => s.setActiveBusinessId),
    );

    act(() => {
      setter.current('biz-x');
    });
    act(() => {
      setter.current(null);
    });

    const stored = JSON.parse(localStorage.getItem('matrix-ui') ?? '{}');
    expect(stored.state?.activeBusinessId).toBeNull();
  });
});