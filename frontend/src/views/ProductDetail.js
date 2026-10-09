import React, { useEffect, useState } from 'react';
import axios from 'axios';
import StockProjectionChart from '../components/StockProjectionChart';

/**
 * Product page: shows when the product is expected to reach its reorder level and run out.
 * Requests must carry the user's token, e.g. axios.defaults.headers.common.Authorization = `Bearer ${accessToken}`.
 *
 * @param {Object} props
 * @param {number} props.productId
 * @param {number} [props.days=30] - How far ahead to project (1-180).
 */
const ProductDetail = ({ productId, days = 30 }) => {
  const [projection, setProjection] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    axios
      .get(`/inventory/products/${productId}/stock-projection/`, { params: { days } })
      .then((response) => setProjection(response.data))
      .catch((err) => setError(err.message));
  }, [productId, days]);

  if (error) return <div>Error: {error}</div>;
  if (!projection) return <div>Loading...</div>;

  return (
    <div>
      <h1>{projection.product_name}</h1>
      <p>In stock: {projection.current_stock} (reorder at {projection.reorder_threshold})</p>
      <p>Average daily demand: {projection.average_daily_demand}</p>
      {projection.reorder_date && <p>Reorder by: {new Date(projection.reorder_date).toLocaleDateString()}</p>}
      {projection.stockout_date && <p>Expected to run out: {new Date(projection.stockout_date).toLocaleDateString()}</p>}
      <StockProjectionChart projection={projection.projection} />
    </div>
  );
};

export default ProductDetail;
