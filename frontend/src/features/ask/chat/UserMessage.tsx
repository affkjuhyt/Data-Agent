/** The person's question, right-aligned on subtle. */
export function UserMessage({ text }: { text: string }) {
  return (
    <div className="flex justify-end">
      <p className="m-0 max-w-[80%] whitespace-pre-wrap rounded-lg bg-subtle px-da-lg py-da-sm text-foreground da-body">
        {text}
      </p>
    </div>
  );
}
