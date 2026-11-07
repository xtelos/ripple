import type { ReactNode } from 'react';

export function Badge({ text }: { text: string }) {
  return <span className="badge">{text}</span>;
}

// Renders whatever its caller passes as children, next to a Badge of its own.
export function Panel({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <section>
      <h2>{title}</h2>
      <Badge text="panel" />
      {children}
    </section>
  );
}

// A render prop: the caller supplies the function that makes each row's element.
export function DataList<T>({ rows, render }: { rows: T[]; render: (row: T) => ReactNode }) {
  return (
    <ul>
      {rows.map((row, index) => (
        <li key={index}>{render(row)}</li>
      ))}
    </ul>
  );
}
