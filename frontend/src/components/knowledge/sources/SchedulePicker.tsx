import {
  ScheduleState, ScheduleMode, SCHEDULE_MODE_LABEL, DAY_LABELS, DAY_NAMES, MONTH_NAMES,
  buildCron, cronToHuman,
} from './sourceShared';

interface SchedulePickerProps extends ScheduleState {
  onChange: (next: ScheduleState) => void;
  /** Text shown under the computed cron preview — differs by caller (Add:
   * "runs immediately, then repeats"; Edit: "takes effect next run"). */
  footnote: string;
  /** Set when the source's existing cron came from outside this picker (a
   * hand-edited or unusual cron this app never itself generates) — touching
   * any control here replaces it with whatever the picker computes. */
  unrepresentableCron?: string | null;
}

/** The friendly, no-cron-syntax-required schedule picker — shared by
 * SourceRegistry's Add/Edit forms and IngestionWizard's "From URL" / "From
 * Feed-or-API" / Web Portal methods (the person filling any of these should
 * never need to know cron to read or change a schedule). Monthly and yearly
 * both let a steward pick MULTIPLE days/months rather than offering a
 * separate "twice a month"/"twice a year" mode — publishing on the 1st and
 * the 15th is still "Monthly", just with 2 days ticked. */
export function SchedulePicker(props: SchedulePickerProps) {
  const {
    mode, time, days, monthDays, dayOfMonth, months, hourEvery, minuteEvery,
    onChange, footnote, unrepresentableCron,
  } = props;
  const patch = (next: Partial<ScheduleState>) =>
    onChange({ mode, time, days, monthDays, dayOfMonth, months, hourEvery, minuteEvery, ...next });
  const toggleDay = (day: number) => patch({ days: days.includes(day) ? days.filter(d => d !== day) : [...days, day].sort((a, b) => a - b) });
  // At least one day/month must stay selected — an empty selection has no
  // meaningful cron to fall back to.
  const toggleMonthDay = (d: number) => {
    if (monthDays.includes(d)) {
      if (monthDays.length === 1) return;
      patch({ monthDays: monthDays.filter(x => x !== d) });
    } else {
      patch({ monthDays: [...monthDays, d].sort((a, b) => a - b) });
    }
  };
  const toggleMonth = (m: number) => {
    if (months.includes(m)) {
      if (months.length === 1) return;
      patch({ months: months.filter(x => x !== m) });
    } else {
      patch({ months: [...months, m].sort((a, b) => a - b) });
    }
  };

  return (
    <div>
      <label className="text-[11px] font-bold uppercase tracking-wider text-[#8A5A00] block mb-1.5">Schedule</label>
      {unrepresentableCron && (
        <div className="text-[10px] font-mono text-amber-700 bg-amber-50 border border-amber-200 rounded px-2 py-1 mb-2">
          Current schedule ({cronToHuman(unrepresentableCron)}) was set outside this picker. Choosing an option below replaces it.
        </div>
      )}
      <div className="flex items-center gap-1.5 flex-wrap mb-3">
        {(Object.keys(SCHEDULE_MODE_LABEL) as ScheduleMode[]).map(m => (
          <button
            key={m}
            type="button"
            onClick={() => patch({ mode: m })}
            className={`px-3 py-1.5 rounded-full text-[11px] font-bold border transition-colors whitespace-nowrap ${
              mode === m ? 'bg-[#B88719] border-[#B88719] text-white' : 'bg-white border-[#BFA66A] text-[#5F5F5F] hover:bg-[#FFF9E8]'
            }`}
          >
            {SCHEDULE_MODE_LABEL[m]}
          </button>
        ))}
      </div>

      {mode === 'none' && (
        <div className="text-[11px] font-mono text-[#8A8A8A]">
          Never runs on a timer — only via "Run Now". Good for a true one-off, or a source with no fixed publication cadence.
        </div>
      )}

      {mode === 'hourly' && (
        <div className="flex flex-col gap-1.5">
          <div className="flex items-center gap-4 flex-wrap">
            <label className="flex items-center gap-1.5 text-xs text-[#3F3F3F]">
              Hour
              <input
                type="number" min={0} max={23}
                className="ui-input w-16 text-xs px-2 py-1 font-mono"
                value={hourEvery}
                onChange={e => patch({ hourEvery: Math.min(23, Math.max(0, parseInt(e.target.value, 10) || 0)) })}
              />
            </label>
            <label className="flex items-center gap-1.5 text-xs text-[#3F3F3F]">
              Minute
              <input
                type="number" min={0} max={59}
                className="ui-input w-16 text-xs px-2 py-1 font-mono"
                value={minuteEvery}
                onChange={e => patch({ minuteEvery: Math.min(59, Math.max(0, parseInt(e.target.value, 10) || 0)) })}
              />
            </label>
          </div>
          <span className="text-[10px] font-mono text-[#8A8A8A]">
            Hour = 0 → repeat every [Minute] minutes. Hour &gt; 0 → repeat every [Hour] hours, at that minute past the hour.
          </span>
        </div>
      )}

      {mode === 'weekly' && (
        <div className="flex items-center gap-4 flex-wrap">
          <input
            type="time"
            className="ui-input text-xs px-3 py-2 font-mono w-32"
            value={time}
            onChange={e => patch({ time: e.target.value })}
          />
          <div className="flex items-center gap-1.5">
            {DAY_LABELS.map((label, day) => {
              const active = days.includes(day);
              return (
                <button
                  key={day}
                  type="button"
                  title={DAY_NAMES[day]}
                  onClick={() => toggleDay(day)}
                  className={`w-7 h-7 rounded-full text-[11px] font-bold border transition-colors ${
                    active ? 'bg-[#B88719] border-[#B88719] text-white' : 'bg-white border-[#BFA66A] text-[#5F5F5F] hover:bg-[#FFF9E8]'
                  }`}
                >
                  {label}
                </button>
              );
            })}
          </div>
        </div>
      )}

      {mode === 'monthly' && (
        <div className="flex flex-col gap-2">
          <input
            type="time"
            className="ui-input text-xs px-3 py-2 font-mono w-32"
            value={time}
            onChange={e => patch({ time: e.target.value })}
          />
          <div className="flex flex-col gap-1">
            <span className="text-[10px] font-mono text-[#8A8A8A]">Days of month — tick one or more (e.g. 1st and 15th)</span>
            <div className="flex items-center gap-1 flex-wrap max-w-md">
              {Array.from({ length: 31 }, (_, i) => i + 1).map(d => {
                const active = monthDays.includes(d);
                return (
                  <button
                    key={d}
                    type="button"
                    onClick={() => toggleMonthDay(d)}
                    className={`w-7 h-7 rounded-md text-[10px] font-bold border transition-colors ${
                      active ? 'bg-[#B88719] border-[#B88719] text-white' : 'bg-white border-[#BFA66A] text-[#5F5F5F] hover:bg-[#FFF9E8]'
                    }`}
                  >
                    {d}
                  </button>
                );
              })}
            </div>
          </div>
        </div>
      )}

      {mode === 'quarterly' && (
        <div className="flex items-center gap-4 flex-wrap">
          <input
            type="time"
            className="ui-input text-xs px-3 py-2 font-mono w-32"
            value={time}
            onChange={e => patch({ time: e.target.value })}
          />
          <label className="flex items-center gap-1.5 text-xs text-[#3F3F3F]">
            Day of month
            <input
              type="number" min={1} max={31}
              className="ui-input w-16 text-xs px-2 py-1 font-mono"
              value={dayOfMonth}
              onChange={e => patch({ dayOfMonth: Math.min(31, Math.max(1, parseInt(e.target.value, 10) || 1)) })}
            />
          </label>
        </div>
      )}

      {mode === 'yearly' && (
        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-4 flex-wrap">
            <input
              type="time"
              className="ui-input text-xs px-3 py-2 font-mono w-32"
              value={time}
              onChange={e => patch({ time: e.target.value })}
            />
            <label className="flex items-center gap-1.5 text-xs text-[#3F3F3F]">
              Day of month
              <input
                type="number" min={1} max={31}
                className="ui-input w-16 text-xs px-2 py-1 font-mono"
                value={dayOfMonth}
                onChange={e => patch({ dayOfMonth: Math.min(31, Math.max(1, parseInt(e.target.value, 10) || 1)) })}
              />
            </label>
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-[10px] font-mono text-[#8A8A8A]">Months — tick one or more (e.g. Jan and Jul)</span>
            <div className="flex items-center gap-1.5 flex-wrap">
              {MONTH_NAMES.map((name, i) => {
                const m = i + 1;
                const active = months.includes(m);
                return (
                  <button
                    key={name}
                    type="button"
                    onClick={() => toggleMonth(m)}
                    className={`px-2.5 py-1 rounded-md text-[10px] font-bold border transition-colors ${
                      active ? 'bg-[#B88719] border-[#B88719] text-white' : 'bg-white border-[#BFA66A] text-[#5F5F5F] hover:bg-[#FFF9E8]'
                    }`}
                  >
                    {name}
                  </button>
                );
              })}
            </div>
          </div>
        </div>
      )}

      {mode !== 'none' && (
        <div className="text-[11px] font-mono text-[#8A8A8A] mt-2">
          {cronToHuman(buildCron(mode, time, days, monthDays, dayOfMonth, months, hourEvery, minuteEvery))}
          <div className="mt-1 text-[#5F5F5F]">{footnote}</div>
        </div>
      )}
    </div>
  );
}
