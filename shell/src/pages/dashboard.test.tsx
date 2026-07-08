import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import axios from 'axios';
import { Dashboard } from './dashboard';
import { mockAdapter } from '@/lib/api-mock';

// Wire the mock adapter so the page renders without a real backend.
axios.defaults.adapter = mockAdapter;

function renderWithProviders() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('Dashboard page', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders the page title and KPI cards', async () => {
    renderWithProviders();
    expect(screen.getByText('总览')).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText('设备')).toBeInTheDocument();
    });
    expect(screen.getByText('账号')).toBeInTheDocument();
    expect(screen.getByText('任务 24h')).toBeInTheDocument();
    expect(screen.getByText('LLM 成本 24h')).toBeInTheDocument();
  });

  it('renders the alerts feed', async () => {
    renderWithProviders();
    await waitFor(() => {
      expect(screen.getByText('告警')).toBeInTheDocument();
    });
  });

  it('renders the natural-language chat input', () => {
    renderWithProviders();
    expect(screen.getByText('自然语言指令')).toBeInTheDocument();
  });
});
