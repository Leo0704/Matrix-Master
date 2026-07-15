import { Link } from 'react-router-dom';
import { Trash2 } from 'lucide-react';
import type { Note } from '@/types/api';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { StatusBadge } from '@/components/common/status-badge';
import { formatDate } from '@/lib/format';

export function NoteCard({ note, onDelete }: { note: Note; onDelete?: () => void }) {
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
        {onDelete && (
          <div className="flex justify-end pt-1">
            <Button
              variant="ghost"
              size="sm"
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                onDelete();
              }}
              className="h-7 px-2 text-xs text-destructive hover:text-destructive"
            >
              <Trash2 className="mr-1 h-3 w-3" /> 删除
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
