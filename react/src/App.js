import React from 'react';
import { CssBaseline, Container, Typography } from '@mui/material';
import Dashboard from './Dashboard';

function App() {
  return (
    <div>
      <CssBaseline />
      <Container maxWidth="lg">
        <Typography variant="h4" component="h1" gutterBottom sx={{ marginTop: 4 }}>
          ダッシュボード
        </Typography>
        <Dashboard />
      </Container>
    </div>
  );
}

export default App;
