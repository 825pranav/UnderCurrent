import React from "react";

export default function SkeletonRow({ cols = 7 }) {
  return (
    <tr>
      {Array.from({ length: cols }).map((_, i) => (
        <td key={i} className="px-4 py-3">
          <div className="skeleton h-4 rounded w-full" style={{ maxWidth: i === cols - 1 ? "80%" : "100%" }} />
        </td>
      ))}
    </tr>
  );
}
