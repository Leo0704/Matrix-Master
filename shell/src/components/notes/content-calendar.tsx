import { useMemo } from 'react';
import type { Note } from '@/types/api';
import { cn } from '@/lib/utils';
import { formatDate } from '@/lib/format';

interface ContentCalendarProps {
  notes: Note[];
  days?: number;
}

export function ContentCalendar({ notes, days = 14 }: ContentCalendarProps) {
  const start = useMemo(() => {
    const d = new Date();
    d.setHours(0, 0, 0, 0);
    return d;
  }, []);

  const cells = useMemo(() => {
    return Array.from({ length: days }, (_, i) => {
      const d = new Date(start);
      d.setDate(start.getDate() + i);
      const day = d.toISOString().slice(0, 10);
      const dayNotes = notes.filter((n) => {
        const ts = n.scheduled_at ?? n.published_at;
        return ts?.slice(0, 10) === day;
      });
      return { date: d, day, notes: dayNotes };
    });
  }, [notes, days, start]);

  return (
    <div className="grid grid-cols-7 gap-2">
      {cells.map(({ date, day, notes: dayNotes }) => (
        <div
          key={day}
          className={cn(
            'flex min-h-[80px] flex-col gap-1 rounded-md border p-2 text-xs',
            dayNotes.length > 0 ? 'bg-accent/30' : 'bg-muted/20',
          )}
        >
          <div className="flex items-center justify-between text-muted-foreground">
            <span className="font-mono">{formatDate(date.toISOString(), 'MM/dd')}</span>
            <span>{date.getDate() === new Date().getDate() && '今天'}</span>
          </div>
          {dayNotes.slice(0, 2).map((n) => (
            <div key={n.id} className="truncate rounded bg-background px-1 py-0.5" title={n.title}>
              {n.title}
            </div>
          ))}
          {dayNotes.length > 2 && (
            <div className="text-muted-foreground">+{dayNotes.length - 2}</div>
          )}
        </div>
      ))}
    </div>
  );
}
