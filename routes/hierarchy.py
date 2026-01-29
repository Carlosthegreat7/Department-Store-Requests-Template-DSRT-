from flask import Blueprint, render_template, request, redirect, url_for, flash
from extensions import db
from models import Brand
from portal import loggedin_required
from sqlalchemy.exc import IntegrityError

#Blueprint for Hierarchy (Brand)
hierarchy_bp = Blueprint('hierarchy', __name__)

@hierarchy_bp.route('/admin/add_hierarchy', methods=['GET', 'POST'])
@loggedin_required()
def add_hierarchy():
    if request.method == 'POST':
        # Capture and Pre-validate lengths
        pg_code = request.form.get('product_group', '').strip().upper()
        if len(pg_code) > 20:
            flash("Product Group code is too long (Max 20 chars).", "warning")
            return render_template('add_hierarchy.html')

        try:
            new_brand = Brand(
                brand_name=request.form.get('brand_name', '').strip().upper(),
                product_group=pg_code,
                dept_code=request.form.get('dept_code', '').strip().zfill(3),
                sub_dept_code=request.form.get('sub_dept_code', '').strip().zfill(3),
                class_code=request.form.get('class_code', '').strip().zfill(3)
            )
            db.session.add(new_brand)
            db.session.commit()
            flash(f"Successfully added Brand: {new_brand.brand_name}", "success")
            return redirect(url_for('admin_management', _anchor='hierarchy'))
            
        except IntegrityError:
            db.session.rollback()
            flash(f"Error: The Product Group '{pg_code}' is already registered to another brand.", "danger")
        except Exception as e:
            db.session.rollback()
            flash(f"System Error: {str(e)}", "danger")
            
    return render_template('add_hierarchy.html')

@hierarchy_bp.route('/admin/edit_hierarchy/<code>', methods=['GET', 'POST'])
@loggedin_required()
def edit_hierarchy(code):
    """Route to update an existing Brand entry."""
    # Find the hierarchy entry by the old brand name (code)
    brand = Brand.query.filter_by(brand_name=code).first_or_404()
    
    if request.method == 'POST':
        try:
            # Update values using names in edit_hierarchy.html
            brand.brand_name = request.form.get('brand_name', '').strip().upper()
            brand.product_group = request.form.get('product_group', '').strip().upper()
            brand.dept_code = request.form.get('dept_code', '').strip().zfill(3)
            brand.sub_dept_code = request.form.get('sub_dept_code', '').strip().zfill(3)
            brand.class_code = request.form.get('class_code', '').strip().zfill(3)
            
            db.session.commit()
            flash(f"Hierarchy for {brand.brand_name} updated successfully!", "success")
            # FIXED: Added _anchor to persist the Hierarchy tab state
            return redirect(url_for('admin_management', _anchor='hierarchy'))
            
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {e}", "danger")
            
    # Passing variable as 'hierarchy' to match edit_hierarchy.html
    return render_template('edit_hierarchy.html', hierarchy=brand)

@hierarchy_bp.route('/admin/delete_hierarchy/<code>', methods=['POST'])
@loggedin_required()
def delete_hierarchy(code):
    """Route to remove a Brand entry from the database with dependency checks."""
    # Find by brand_name
    brand = Brand.query.filter_by(brand_name=code).first()
    
    if brand:
        try:
            db.session.delete(brand)
            db.session.commit()
            flash(f"Successfully removed '{code}' from Hierarchy records.", "success")
            
        except IntegrityError:
            db.session.rollback()
            # This triggers if the SQL Foreign Key constraint prevents the delete
            flash(f"Deletion Failed: '{code}' is currently linked to existing sub-classes or items.", "danger")
            
        except Exception as e:
            db.session.rollback()
            flash(f"System Error during deletion: {str(e)}", "danger")
    else:
        flash("Record not found or already deleted.", "warning")
        
    return redirect(url_for('admin_management', _anchor='hierarchy'))