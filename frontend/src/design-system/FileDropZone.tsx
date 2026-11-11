import type { ReactNode, Ref } from 'react';
import {
  Button as AriaButton,
  DropZone,
  FileTrigger,
  isFileDropItem,
  type DropItem,
} from 'react-aria-components';
import { Icon } from './Icon';

/*
 * Where files come in: a dashed area files can be dropped on (FileDropZone) and the button that
 * opens the file chooser (FilePicker), usually inside it. React Aria's DropZone and FileTrigger:
 * the zone also takes a paste and a keyboard drop through its own hidden button, and says it is a
 * drop target while a file is over it; the hand-written one only listened to dragover and drop.
 * FileTrigger's input is display:none and out of the accessibility tree: the button's text is
 * what names the choice.
 */
const filesOf = async (items: DropItem[]) =>
  Promise.all(items.filter(isFileDropItem).map((item) => item.getFile()));

export function FileDropZone({
  label,
  onFiles,
  className,
  children,
}: {
  /** Names the zone's drop button for screen readers ("Thả tệp để nạp"). */
  label: string;
  onFiles: (files: File[]) => void;
  /** Drawn by whether a file is over it. */
  className: (state: { isDropTarget: boolean }) => string;
  children: ReactNode;
}) {
  return (
    <DropZone
      aria-label={label}
      onDrop={async (event) => {
        const files = await filesOf(event.items);
        if (files.length) onFiles(files);
      }}
      className={({ isDropTarget }) => className({ isDropTarget })}
    >
      {children}
    </DropZone>
  );
}

export function FilePicker({
  acceptedFileTypes,
  allowsMultiple,
  onFiles,
  inputRef,
  loading,
  className,
  children,
}: {
  /** Extensions or MIME types the chooser offers (".csv", "application/pdf"). */
  acceptedFileTypes?: string[];
  allowsMultiple?: boolean;
  onFiles: (files: File[]) => void;
  inputRef?: Ref<HTMLInputElement>;
  /** A spinner in place of the content's start, and no second press while it turns. */
  loading?: boolean;
  /** The button's look, e.g. buttonClass({ kind: 'primary' }). */
  className: string;
  children: ReactNode;
}) {
  return (
    <FileTrigger
      ref={inputRef}
      acceptedFileTypes={acceptedFileTypes}
      allowsMultiple={allowsMultiple}
      onSelect={(list) => {
        if (list && list.length) onFiles(Array.from(list));
      }}
    >
      <AriaButton className={className} isPending={loading}>
        {loading && <Icon name="refresh" className="animate-spin" />}
        {children}
      </AriaButton>
    </FileTrigger>
  );
}
