import { useEffect, useRef } from "react"
import { Message } from "../../lib/types"
import { UserMessage } from "./UserMessage"
import { AIMessage } from "./AIMessage"

export function MessageList({ messages, conversationId }: { messages: Message[]; conversationId: string }) {
  const bottomRef = useRef<HTMLDivElement>(null)
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }) }, [messages.length])
  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto max-w-3xl px-4 py-6 flex flex-col gap-5">
        {messages.map((msg) =>
          msg.role === "user"
            ? <UserMessage key={msg.id} message={msg} />
            : <AIMessage key={msg.id} message={msg} conversationId={conversationId} />
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  )
}
