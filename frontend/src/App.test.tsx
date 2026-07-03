import { render, screen } from '@testing-library/react'
import App from './App'

test('renders brand text', () => {
  render(<App />)
  expect(screen.getByText('廷豐智能研報')).toBeInTheDocument()
})
