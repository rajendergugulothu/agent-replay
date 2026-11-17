import type { ScheduleEntry } from '../api/types';

interface Props {
  schedule: ScheduleEntry[];
}

export function ScheduleTable({ schedule }: Props): JSX.Element {
  if (schedule.length === 0) return <p className="muted">The agent submitted an empty schedule.</p>;
  return (
    <table className="table table--compact">
      <caption className="visually-hidden">Final schedule</caption>
      <thead>
        <tr>
          <th scope="col">Course</th>
          <th scope="col">Days</th>
          <th scope="col">Time</th>
          <th scope="col" className="num">
            Credits
          </th>
        </tr>
      </thead>
      <tbody>
        {schedule.map((entry) => (
          <tr key={entry.section_id}>
            <th scope="row">
              {entry.course_code}
              <span className="table__sub">{entry.title}</span>
            </th>
            <td>{entry.days.join(' ')}</td>
            <td className="nowrap">
              {entry.start} to {entry.end}
            </td>
            <td className="num">{entry.credits}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
