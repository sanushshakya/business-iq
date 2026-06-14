import React from 'react';
import LineChart from 'recharts/lib/cartesian/LineChart';
import XAxis from 'recharts/lib/cartesian/XAxis';
import YAxis from 'recharts/lib/cartesian/YAxis';
import CartesianGrid from 'recharts/lib/cartesian/CartesianGrid';
import Line from 'recharts/lib/cartesian/Line';
import Tooltip from 'recharts/lib/component/Tooltip';

// Define the ProductDetail component
const ProductDetail = ({ stockProjectionData }) => {
  // Extract data for the chart
  const projections = stockProjectionData.map(data => ({
    date: new Date(data.date_projected).toLocaleDateString(),
    price: data.projected_price,
  }));

  return (
    <div>
      <h1>Product Detail</h1>
      <LineChart width={600} height={300} data={projections}>
        <CartesianGrid strokeDasharray="3 3" />
        <XAxis dataKey="date" />
        <YAxis />
        <Tooltip />
        <Line type="monotone" dataKey="price" stroke="#8884d8" />
      </LineChart>
    </div>
  );
};

export default ProductDetail;