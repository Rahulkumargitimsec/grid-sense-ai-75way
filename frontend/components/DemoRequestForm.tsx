'use client';

import { FormEvent, useState } from 'react';

export function DemoRequestForm() {
  const [submitted, setSubmitted] = useState(false);

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSubmitted(true);
  };

  if (submitted) {
    return <div className="demo-success" role="status"><strong>Demo request received.</strong><span>Our grid intelligence team will follow up with you shortly.</span></div>;
  }

  return (
    <form className="demo-form" onSubmit={handleSubmit}>
      <div className="form-field"><label htmlFor="demo-name">Name</label><input id="demo-name" name="name" required /></div>
      <div className="form-field"><label htmlFor="demo-email">Work email</label><input id="demo-email" name="email" type="email" required /></div>
      <div className="form-field"><label htmlFor="demo-organization">Organization</label><input id="demo-organization" name="organization" required /></div>
      <div className="form-field"><label htmlFor="demo-message">What would you like to explore?</label><textarea id="demo-message" name="message" rows={4} required /></div>
      <button className="primary-button" type="submit">Request a demo</button>
    </form>
  );
}
