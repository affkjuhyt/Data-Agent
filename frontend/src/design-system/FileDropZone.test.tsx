import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { FileDropZone, FilePicker } from './FileDropZone';

function Zone({ onFiles }: { onFiles: (files: File[]) => void }) {
  return (
    <FileDropZone
      label="Thả tệp để nạp"
      onFiles={onFiles}
      className={({ isDropTarget }) => (isDropTarget ? 'zone over' : 'zone')}
    >
      <FilePicker allowsMultiple acceptedFileTypes={['.csv']} onFiles={onFiles} className="pick">
        Chọn tệp
      </FilePicker>
    </FileDropZone>
  );
}

// jsdom has no DataTransfer: this carries the files the way a browser drag does.
const transferOf = (files: File[]) => ({
  types: ['Files'],
  files,
  items: files.map((file) => ({ kind: 'file', type: file.type, getAsFile: () => file })),
  dropEffect: 'none',
  effectAllowed: 'all',
  getData: () => '',
});

describe('FileDropZone', () => {
  it('hands over the files chosen from its button', async () => {
    const user = userEvent.setup();
    const onFiles = vi.fn();
    const { container } = render(<Zone onFiles={onFiles} />);
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    expect(input).toHaveAttribute('accept', '.csv');
    expect(input).toHaveAttribute('multiple');
    const file = new File(['a,b'], 'chi_tieu.csv', { type: 'text/csv' });
    await user.upload(input, file);
    expect(onFiles).toHaveBeenCalledWith([file]);
  });

  it('shows it is a target while a file is over it, and hands over the dropped files', async () => {
    const onFiles = vi.fn();
    const { container } = render(<Zone onFiles={onFiles} />);
    const zone = container.firstElementChild as HTMLElement;
    const file = new File(['%PDF'], 'HS_LD2409_0187.pdf', { type: 'application/pdf' });
    const dataTransfer = transferOf([file]);
    fireEvent.dragEnter(zone, { dataTransfer, clientX: 1, clientY: 1 });
    fireEvent.dragOver(zone, { dataTransfer, clientX: 1, clientY: 1 });
    expect(zone).toHaveClass('over');
    fireEvent.drop(zone, { dataTransfer, clientX: 1, clientY: 1 });
    await waitFor(() => expect(onFiles).toHaveBeenCalledWith([file]));
    expect(zone).not.toHaveClass('over');
    expect(screen.getByRole('button', { name: 'Chọn tệp' })).toBeInTheDocument();
  });
});
