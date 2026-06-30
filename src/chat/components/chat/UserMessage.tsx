import { format } from "date-fns"
import { Message } from "../../lib/types"

export function UserMessage({ message }: { message: Message }) {
  return (
    <div className="flex justify-end gap-3">
      <div className="flex flex-col items-end gap-1 max-w-[75%]">
        <div className="rounded-2xl rounded-tr-sm bg-primary px-4 py-2.5 text-sm text-primary-foreground leading-relaxed">
          {message.content}
        </div>
        <span className="text-[10px] text-muted-foreground">{format(new Date(message.timestamp), "HH:mm")}</span>
      </div>
      <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full bg-blue-600 text-xs font-semibold text-white">HL</div>
    </div>
  )
}
