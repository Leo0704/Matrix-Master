/**
 * BusinessSelector 组件测试（v0.7+ 业务模型重构）。
 *
 * 验证：
 * - 显示当前活跃业务名（或 fallback "选择业务"）
 * - 点下拉打开业务列表
 * - 选中触发 onChange + 派发 matrix:business-changed 事件
 * - 无业务时显示空状态 + 跳 /businesses 链接
 */
import { describe, expect, it, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { BusinessSelector } from '@/components/layout/topbar';
import { useUIStore } from '@/stores/ui-store';

const sampleBusinesses = [
  { id: 'biz-a', name: '业务 A', slug: 'biz-a' },
  { id: 'biz-b', name: '业务 B', slug: 'biz-b' },
];

function renderWithRouter(ui: React.ReactElement) {
  return render(<MemoryRouter>{ui}</MemoryRouter>);
}

describe('BusinessSelector', () => {
  beforeEach(() => {
    localStorage.clear();
    useUIStore.setState({ activeBusinessId: null });
  });

  it('未选业务时显示 "选择业务"', () => {
    renderWithRouter(
      <BusinessSelector
        businesses={sampleBusinesses}
        activeId={null}
        activeName={null}
        onChange={() => {}}
      />,
    );
    expect(screen.getByText('选择业务')).toBeInTheDocument();
  });

  it('选了业务时显示业务名', () => {
    renderWithRouter(
      <BusinessSelector
        businesses={sampleBusinesses}
        activeId="biz-a"
        activeName="业务 A"
        onChange={() => {}}
      />,
    );
    expect(screen.getByText('业务 A')).toBeInTheDocument();
  });

  it('点下拉打开业务列表', () => {
    renderWithRouter(
      <BusinessSelector
        businesses={sampleBusinesses}
        activeId={null}
        activeName={null}
        onChange={() => {}}
      />,
    );
    const trigger = screen.getByRole('button');
    fireEvent.click(trigger);
    expect(screen.getAllByText('业务 A').length).toBeGreaterThan(0);
    expect(screen.getAllByText('业务 B').length).toBeGreaterThan(0);
  });

  it('选业务触发 onChange + matrix:business-changed 事件', () => {
    const onChange = vi.fn();
    const eventListener = vi.fn();
    window.addEventListener('matrix:business-changed', eventListener);

    renderWithRouter(
      <BusinessSelector
        businesses={sampleBusinesses}
        activeId={null}
        activeName={null}
        onChange={onChange}
      />,
    );

    // 打开下拉
    fireEvent.click(screen.getByRole('button'));
    // 选 "业务 B"
    const allBizB = screen.getAllByText('业务 B');
    // 最后一个 "业务 B" 是下拉项
    fireEvent.click(allBizB[allBizB.length - 1]!);

    expect(onChange).toHaveBeenCalledWith('biz-b');
    expect(eventListener).toHaveBeenCalledOnce();
    const event = eventListener.mock.calls[0]?.[0] as CustomEvent;
    expect(event.detail?.businessId).toBe('biz-b');

    window.removeEventListener('matrix:business-changed', eventListener);
  });

  it('空业务列表显示去新建链接', () => {
    renderWithRouter(
      <BusinessSelector
        businesses={[]}
        activeId={null}
        activeName={null}
        onChange={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole('button'));
    expect(screen.getByText(/还没有业务/)).toBeInTheDocument();
    expect(screen.getByText('去新建')).toBeInTheDocument();
  });
});