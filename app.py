from flask import Flask, request, render_template, Response, jsonify, url_for, redirect, session
import cv2
from ultralytics import YOLO
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from flask_migrate import Migrate
import os
from PIL import Image
from datetime import datetime, timedelta
from collections import OrderedDict
from flask import flash
from dotenv import load_dotenv

load_dotenv()



# Create Flask app
app = Flask(__name__)

# Secret key for session management
app.secret_key = os.getenv("SECRET_KEY")

# Configure PostgreSQL database
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv("DATABASE_URL")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize database and migration
db = SQLAlchemy(app)
migrate = Migrate(app, db)

# Farmer model for registration and login
class Farmer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    first_name = db.Column(db.String(50), nullable=False)
    last_name = db.Column(db.String(50), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(128), nullable=False)
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())

## Plot model for land plots
class Plot(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    farmer_id = db.Column(db.Integer, db.ForeignKey('farmer.id'), nullable=False)
    plot_size = db.Column(db.Float, nullable=False)  # Define plot size in square meters
    planting_date = db.Column(db.Date, nullable=False)
    plot_coordinates = db.Column(db.Text, nullable=False)  # Add this line to store coordinates
    ripeness_estimated = db.Column(db.Date, nullable=True)
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())


# Plantation model for plantation scheduling
class Plantation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    plot_id = db.Column(db.Integer, db.ForeignKey('plot.id'), nullable=False)
    planting_date = db.Column(db.Date, nullable=False)
    ripeness_estimated = db.Column(db.Date)
    land_preparation_date = db.Column(db.Date, nullable=True)
    maintenance_date = db.Column(db.Date, nullable=True)
    ripening_date = db.Column(db.Date, nullable=True)
    harvesting_date = db.Column(db.Date, nullable=True)
    market_access_date = db.Column(db.Date, nullable=True)
    

# Load your trained YOLO model
model = YOLO('models/best.pt')

# Define folder to store uploaded images
UPLOAD_FOLDER = 'static/uploads/'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Login route
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']

        # Find the farmer by email
        farmer = Farmer.query.filter_by(email=email).first()

        # Check if farmer exists and if password is correct
        if farmer and check_password_hash(farmer.password, password):
            session['farmer_id'] = farmer.id  # Store user ID in session
            return redirect(url_for('dashboard'))
        else:
            return "Login failed. Check your credentials.", 401

    return render_template('login.html')

# Registration route
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        first_name = request.form['first_name']
        last_name = request.form['last_name']
        email = request.form['email']
        password = request.form['password']

        # Hash the password before storing it
        hashed_password = generate_password_hash(password, method='pbkdf2:sha256')

        # Add the new farmer to the database
        new_farmer = Farmer(first_name=first_name, last_name=last_name, email=email, password=hashed_password)
        db.session.add(new_farmer)
        db.session.commit()

        return redirect(url_for('login'))

    return render_template('register.html')

@app.route('/plot_land', methods=['GET', 'POST'])
def plot_land():
    # Check if user is logged in
    if 'farmer_id' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        # Retrieve farmer ID from session and form data
        farmer_id = session.get('farmer_id')
        plot_coordinates = request.form.get('plot_coordinates')
        plot_size = request.form.get('plot_size')
        planting_date_str = request.form.get('planting_date')

        # Validate required fields
        if not (plot_coordinates and plot_size and planting_date_str):
            return jsonify({'error': 'All fields are required.'}), 400

        # Convert planting date to a datetime object
        try:
            planting_date = datetime.strptime(planting_date_str, '%Y-%m-%d')
        except ValueError:
            return jsonify({'error': 'Invalid date format. Use YYYY-MM-DD.'}), 400

        # Estimate ripeness date (example: 540 days after planting)
        ripeness_estimated = planting_date + timedelta(days=540)

        # Create a new Plot entry
        plot = Plot(
            farmer_id=farmer_id,
            plot_size=float(plot_size),
            planting_date=planting_date,
            ripeness_estimated=ripeness_estimated,
            plot_coordinates=plot_coordinates
        )

        # Add to database
        db.session.add(plot)
        db.session.commit()

        # Redirect to schedule plantation calculation with necessary parameters
        return redirect(url_for('schedule_plantation', plot_id=plot.id))

    return render_template('plot_land.html')



@app.route('/save_plot', methods=['POST'])
def save_plot():
    plot_size = request.form.get('plot_size')
    planting_date = request.form.get('planting_date')
    plot_coordinates = request.form.get('plot_coordinates')

    if not plot_size or not planting_date or not plot_coordinates:
        return "Error: Missing plot data", 400
    
    farmer_id = session.get('farmer_id')
    if farmer_id:
        new_plot = Plot(
            farmer_id=farmer_id,
            plot_size=float(plot_size),
            planting_date=datetime.strptime(planting_date, '%Y-%m-%d'),
            plot_coordinates=plot_coordinates
        )
        db.session.add(new_plot)
        db.session.commit()
        
        # Redirect to schedule plantation with the plot ID
        return redirect(url_for('schedule_plantation', plot_id=new_plot.id))
    
    return "Error saving plot data", 400




@app.route('/schedule_plantation')
def schedule_plantation():
    plot_id = request.args.get('plot_id')

    # Use db.session.get() to avoid legacy API warnings in SQLAlchemy 2.x
    plot = db.session.get(Plot, plot_id)

    if not plot:
        flash("Plot not found.", "danger")
        return redirect(url_for('plot_land'))

    planting_date = plot.planting_date

    # Calculate schedule dates based on the planting_date
    land_preparation_date = planting_date - timedelta(days=7)
    maintenance_date = planting_date + timedelta(days=180)
    ripening_date = planting_date + timedelta(days=365)
    harvesting_date = planting_date + timedelta(days=540)
    market_access_date = planting_date + timedelta(days=570)

    # Create the Plantation instance with all necessary fields
    plantation = Plantation(
        plot_id=plot.id,
        planting_date=planting_date,  # Explicitly set planting_date
        land_preparation_date=land_preparation_date,
        maintenance_date=maintenance_date,
        ripening_date=ripening_date,
        harvesting_date=harvesting_date,
        market_access_date=market_access_date
    )

    # Add to session and commit to save in the database
    db.session.add(plantation)
    db.session.commit()

    # Redirect to dashboard or schedule page
    return redirect(url_for('dashboard'))  # Redirect to an existing endpoint
@app.route('/check_schedule')
def check_schedule():
    farmer_id = session.get('farmer_id')
    if not farmer_id:
        return "Not logged in", 400
    
    plot = Plot.query.filter_by(farmer_id=farmer_id).first()
    if not plot:
        return "Plot not found", 400

    plantation = Plantation.query.filter_by(plot_id=plot.id).first()
    if plantation:
        schedule_data = {
            "Land Preparation": plantation.land_preparation_date,
            "Planting": plantation.planting_date,
            "Maintenance (Growth Period)": plantation.maintenance_date,
            "Ripening Period": plantation.ripening_date,
            "Harvesting": plantation.harvesting_date,
            "Market Access": plantation.market_access_date
        }
        return jsonify(schedule_data)
    else:
        return "No plantation data found", 400



@app.route('/schedule_page')
def schedule_page():
    plot_id = request.args.get('plot_id')
    plot = Plot.query.get(plot_id)
    plantation = Plantation.query.filter_by(plot_id=plot_id).first()

    if not plot or not plantation:
        flash("Schedule not found.", "danger")
        return redirect(url_for('plot_land'))

    # Schedule dictionary for template
    schedule = {
        "Land Preparation": plantation.land_preparation_date,
        "Planting": plot.planting_date,
        "Maintenance": plantation.maintenance_date,
        "Ripening": plantation.ripening_date,
        "Harvesting": plantation.harvesting_date,
        "Market Access": plantation.market_access_date
    }

    return render_template('schedule_page.html', plot=plot, schedule=schedule)

  


@app.route('/dashboard')
def dashboard():
    if 'farmer_id' not in session:
        return redirect(url_for('login'))

    farmer_id = session['farmer_id']
    plot = Plot.query.filter_by(farmer_id=farmer_id).first()
    plantation = Plantation.query.filter_by(plot_id=plot.id).first() if plot else None

    # Create a schedule dictionary
    schedule = {
        "Land Preparation": plantation.land_preparation_date,
        "Planting": plantation.planting_date,
        "Maintenance (Growth Period)": plantation.maintenance_date,
        "Ripening Period": plantation.ripening_date,
        "Harvesting": plantation.harvesting_date,
        "Market Access": plantation.market_access_date
    } if plantation else None
    print("Schedule data for dashboard:", schedule)

    return render_template('dashboard.html', plot=plot, schedule=schedule)

@app.route('/calculate_plants', methods=['POST'])
def calculate_plants():
    # Get the land size input from the form
    land_size = float(request.form.get('land_size'))  # In square meters
    
    # Define spacing values
    spacing_between_rows = 1.0  # meters
    spacing_between_plants = 0.3  # meters

    # Calculate the number of plants
    if land_size > 0:
        total_plants = land_size / (spacing_between_rows * spacing_between_plants)
        total_plants = int(total_plants)  # Round to nearest whole number
        return render_template('distance_result.html', total_plants=total_plants)
    else:
        return "Invalid land size. Please enter a positive value.", 400

# Main route
@app.route('/')
def index():
 

    return render_template('index.html')  # Render homepage HTML

@app.route('/about')
def about():
    return render_template('about.html')

@app.route('/blogs')
def blogs():
    return render_template('blogs.html')
@app.route('/blog1')
def blog1():
    return render_template('blog1.html')

# You can add additional routes for other blogs in the same way
@app.route('/blog2')
def blog2():
    return render_template('blog2.html')

@app.route('/blog3')
def blog3():
    return render_template('blog3.html')

@app.route('/blog4')
def blog4():
    return render_template('blog4.html')

@app.route('/contact')
def contact():
    return render_template('contact.html')

# Route for handling image uploads and YOLO inference
@app.route('/upload', methods=['POST'])
def upload_file():
    if 'farmer_id' not in session:
        return redirect(url_for('login'))

    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    # Save the uploaded file
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
    file.save(file_path)

    # Run YOLO inference on the uploaded image
    results = model.predict(source=file_path, save=True)

    # Ensure there is at least one detection
    if results[0].boxes:
        detected_class = results[0].names[results[0].boxes.cls[0].item()]  # Extract the class name
    else:
        detected_class = "No detection"

    # Get path of the saved inference result
    result_image_path = os.path.join('static/uploads', os.path.basename(results[0].path))

    # Redirect to results page with the path to the result image
    return render_template('result.html', file_path=result_image_path, detected_class=detected_class)

# Route for handling image capture from webcam
@app.route('/upload_webcam', methods=['POST'])
def upload_webcam():
    if 'farmer_id' not in session:
        return redirect(url_for('login'))

    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    file = request.files['file']

    # Save the captured image from the webcam
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], 'webcam_capture.jpg')
    file.save(file_path)

    # Resize the image to 640x640 to match YOLO input dimensions
    image = Image.open(file_path)
    if image.mode == 'RGBA':
        image = image.convert('RGB')
    image = image.resize((1280, 720))  # Resize to input size expected by YOLO
    image.save(file_path)

    # Run YOLO inference on the captured webcam image
    results = model.predict(source=file_path, save=True)

    # Check if any detection was made
    if results[0].boxes:
        detected_class = results[0].names[results[0].boxes.cls[0].item()]
    else:
        detected_class = "No detection"

    # Return the detected class and the result URL
    result_image_path = os.path.join('static/uploads', os.path.basename(results[0].path))
    return jsonify({'detected_class': detected_class, 'result_url': url_for('static', filename='uploads/' + os.path.basename(results[0].path))})

@app.route('/result')
def result():
    file_path = request.args.get('file_path')
    detected_class = request.args.get('detected_class')

    if file_path and detected_class:
        return render_template('result.html', file_path=file_path, detected_class=detected_class)
    return "Error: Missing file path or detected class", 400


# Logout route
@app.route('/logout')
def logout():
    session.pop('farmer_id', None)
    return redirect(url_for('login'))

# Run the Flask app
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001)
