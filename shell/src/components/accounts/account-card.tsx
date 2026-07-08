import { Link } from 'react-router-dom';
import type { Account } from '@/types/api';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { StatusBadge } from '@/components/common/status-badge';
import { RiskIndicator } from './risk-indicator';
import { formatRelative } from '@/lib/format';

export function AccountCard({ account }: { account: Account }) {
  return (
    <Card className="transition-shadow hover:shadow-md">
      <CardHeader className="flex flex-row items-start justify-between space-y-0 pb-2">
        <div>
          <CardTitle className="text-base">
            <Link to={`/accounts/${account.id}`} className="hover:underline">
              @{account.handle}
            </Link>
          </CardTitle>
          <p className="text-xs text-muted-foreground">ID: {account.id.slice(0, 8)}…</p>
        </div>
        <StatusBadge status={account.status} />
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        <div className="space-y-1">
          <p className="text-xs text-muted-foreground">风险评分</p>
          <RiskIndicator score={account.risk_score} />
        </div>
        <p className="text-xs text-muted-foreground">
          最后活跃：{formatRelative(account.last_active)}
        </p>
      </CardContent>
    </Card>
  );
}
