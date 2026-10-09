import React from 'react';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend } from 'recharts';

/**
 * Chart of a product's projected stock.
 *
 * @param {Object} props
 * @param {Array<Object>} props.projection - The `projection` array returned by
 *   GET /inventory/products/{id}/stock-projection/: [{ date, projected_quantity, expected_demand, demand_multiplier }].
 */
const StockProjectionChart = ({ projection }) => (
  <LineChart width={600} height={300} data={projection}>
    <CartesianGrid strokeDasharray="3 3" />
    <XAxis dataKey="date" tickFormatter={(date) => new Date(date).toLocaleDateString()} />
    <YAxis domain={[0, 'auto']} />
    <Tooltip />
    <Legend />
    <Line type="monotone" dataKey="projected_quantity" name="Projected stock" stroke="#8884d8" activeDot={{ r: 8 }} />
    <Line type="monotone" dataKey="expected_demand" name="Expected daily demand" stroke="#82ca9d" />
  </LineChart>
);

export default StockProjectionChart;
