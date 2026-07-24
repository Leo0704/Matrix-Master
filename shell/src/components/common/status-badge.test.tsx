import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { StatusBadge } from './status-badge';

describe('StatusBadge', () => {
  it('renders the humanized status by default', () => {
    render(<StatusBadge status="tailscale_degraded" />);
    expect(screen.getByText('网络降级')).toBeInTheDocument();
  });

  it('renders the explicit label when provided', () => {
    render(<StatusBadge status="active" label="运行中" />);
    expect(screen.getByText('运行中')).toBeInTheDocument();
  });

  it('uses success variant for active status', () => {
    const { container } = render(<StatusBadge status="active" />);
    expect(container.firstChild).toHaveClass('bg-success/15');
  });

  it('uses destructive variant for failed status', () => {
    const { container } = render(<StatusBadge status="failed" />);
    expect(container.firstChild).toHaveClass('bg-destructive');
  });
});
