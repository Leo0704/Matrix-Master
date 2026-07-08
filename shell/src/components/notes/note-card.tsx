import { Link } from 'react-router-dom';
import type { Note } from '@/types/api';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { StatusBadge } from '@/components/common/status-badge';
import { formatDate } from '@/lib/format';

export function NoteCard({ note }: { note: Note }) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between space-y-0 pb-2">
        <CardTitle className="line-clamp-2 text-base">
          <Link to={`/notes/${note.id}`} className="hover:underline">
            {note.title}
          </Link>
        </CardTitle>
        <StatusBadge status={note.status} />
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        <p className="line-clamp-2 text-muted-foreground">{note.content}</p>
        <div className="flex items-center justify-between text-xs text-muted-foreground">
          <span>计划：{formatDate(note.scheduled_at)}</span>
          {note.platform_url && (
            <a
              href={note.platform_url}
              target="_blank"
              rel="noreferrer"
              className="text-primary hover:underline"
            >
              查看发布 →
            </a>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
