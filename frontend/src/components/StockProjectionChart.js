import React from 'react';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend } from 'recharts';

/**
 * StockProjectionChart component to display the stock projection chart.
 *
 * @param {Object} props - The component props.
 * @param {Array<Object>} props.projections - Array of stock projections data.
 */
const StockProjectionChart = ({ projections }) => {
  return (
    <LineChart width={600} height={300} data={projections}>
      <CartesianGrid strokeDasharray="3 3" />
      <XAxis dataKey="date_projected" tickFormatter={(date) => new Date(date).toLocaleDateString()} />
      <YAxis domain={['dataMin', 'dataMax']} />
      <Tooltip />
      <Legend />
      <Line type="monotone" dataKey="price" stroke="#8884d8" activeDot={{ r: 8 }} />
      <Line type="monotone" dataKey="projected_price" stroke="#82ca9d" />
    </LineChart>
  );
};

export default StockProjectionChart;